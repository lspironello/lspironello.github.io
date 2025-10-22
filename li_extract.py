#!/usr/bin/env python3

from PIL import Image
import pytesseract
import pdfplumber
import os, re, json, yaml, logging
import argparse
import hashlib
import csv
from urllib.parse import quote
from dateutil.parser import parse
from collections import Counter

# Directory and File Configuration (overridden by config.yaml if present)
BASE_DIR = "/mnt/syn/public/documents/jobs/certs"
DATA_DIR = "_data"
DEBUG_DIR = "_debug"
ASSETS_DIR = os.path.join(DATA_DIR, "assets", "pdfs")

# Suppress pdfminer/FontBox warnings
logging.getLogger('pdfminer').setLevel(logging.ERROR)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('extract_log.txt'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def parse_args():
    parser = argparse.ArgumentParser(description="Extract metadata from PDFs and manage certificates.")
    parser.add_argument('--test', type=int, default=0, help='Limit to N files per provider (0 = no limit)')
    parser.add_argument('--provider', type=str, help='Process only the specified provider (e.g., linkedinlearning)')
    parser.add_argument('--config', default='config.yaml', help='Path to config YAML file')
    parser.add_argument('--generate-skills', action='store_true', help='Generate skills.yml')
    parser.add_argument('--generate-stats', action='store_true', help='Generate course statistics')
    parser.add_argument('--filter-skill', type=str, help='Filter by skill (regex supported)')
    parser.add_argument('--filter-year', type=str, help='Filter by year (e.g., 2025)')
    parser.add_argument('--filter-title', type=str, help='Filter by title (regex supported)')
    parser.add_argument('--output-skills', type=str, choices=['csv', 'text'], help='Export skills format')
    parser.add_argument('--output-courses', type=str, choices=['csv', 'text'], help='Export courses format')
    parser.add_argument('--output-urls', action='store_true', help='Export course URLs to course_urls.csv')
    parser.add_argument('--fetch-urls', action='store_true', help='Generate course URLs from titles')
    parser.add_argument('--rename-udemy', action='store_true', help='Rename Udemy files before processing')
    parser.add_argument('--rename-cybrary', action='store_true', help='Rename Cybrary files before processing')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')
    parser.add_argument('--dry-run', action='store_true', help='Simulate without changes')
    parser.add_argument('--display-config', action='store_true', help='Display config.yaml contents')
    parser.add_argument('--display-files', action='store_true', help='Display generated .yml files')
    return parser.parse_args()

def clean_text(text):
    cleaned = re.sub(r'(.)\1+', r'\1', text)
    return re.sub(r'\s+', ' ', cleaned).strip()

def generate_cert_id(pdf_path):
    with open(pdf_path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()

def generate_course_url(title, provider_id):
    slug = re.sub(r'[^\w\s-]', '', title.lower()).replace(' ', '-').strip('-')
    return {
        'linkedinlearning': f"https://www.linkedin.com/learning/{slug}",
        'udemy': f"https://www.udemy.com/course/{slug}/",
        'cybrary': f"https://www.cybrary.it/course/{slug}/",
        'deeplearningai': f"https://www.deeplearning.ai/short-courses/{slug}/"
    }.get(provider_id, "")

def rename_file(file_path, date, title):
    if date and title:
        base_dir = os.path.dirname(file_path)
        clean_title = re.sub(r'[^\w\-]', '', title).lower().replace(' ', '-')
        date_parts = re.match(r'(\d{4})-(\d{2})-(\d{2})', date)
        if not date_parts:
            date_parts = re.match(r'(\w+)\s+(\d{1,2}),\s+(\d{4})', date)
            if date_parts:
                month = {'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04', 'may': '05', 'jun': '06',
                         'jul': '07', 'aug': '08', 'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12'}.get(
                    date_parts.group(1)[:3].lower(), '01')
                day = date_parts.group(2).zfill(2)
                year = date_parts.group(3)
                date = f"{year}-{month}-{day}"
        new_name = f"{date}-{clean_title}.pdf"
        new_path = os.path.join(base_dir, new_name)
        if file_path != new_path and not os.path.exists(new_path):
            os.rename(file_path, new_path)
            print(f"Renamed {os.path.basename(file_path)} to {new_name}")
        return new_path
    return file_path

def rename_udemy_file(pdf_path, test_mode=False):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[0].extract_text() or ""
        title_match = re.search(r'completed the (.*?)(?: online course)? on', text, re.IGNORECASE)
        date_match = re.search(r'on (\w+\s+\d{1,2},\s+\d{4})', text)
        if title_match and date_match:
            title = title_match.group(1).strip().replace(' ', '-').lower()
            date = date_match.group(1).replace(',', '').replace(' ', '-')
            new_name = f"{date}-{title}.pdf"
            new_path = os.path.join(os.path.dirname(pdf_path), new_name)
            if test_mode:
                logger.info(f"[TEST] Would rename: {os.path.basename(pdf_path)} -> {new_name}")
            elif not os.path.isfile(new_path):
                os.rename(pdf_path, new_path)
                logger.info(f"Renamed: {os.path.basename(pdf_path)} -> {new_name}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error renaming Udemy file {pdf_path}: {e}")
        return False

def rename_cybrary_file(pdf_path, test_mode=False):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = pdf.pages[0].extract_text() or ""
        title_match = re.search(r'provided by Cybrary in\s+(.+?)(?:\n|$)', text, re.DOTALL | re.IGNORECASE)
        date_match = re.search(r'(?:Date of Completion\s+)?(\w+\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})', text)
        if title_match and date_match:
            title = title_match.group(1).strip().replace(' ', '-').lower()
            date_str = date_match.group(1).strip()
            try:
                parsed_date = parse(date_str)
                date = parsed_date.strftime('%Y-%m-%d')
            except:
                mdy_match = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', date_str)
                date = f"{mdy_match.group(3)}-{mdy_match.group(1).zfill(2)}-{mdy_match.group(2).zfill(2)}" if mdy_match else 'unknown-date'
            new_name = f"{date}-{title}.pdf"
            new_path = os.path.join(os.path.dirname(pdf_path), new_name)
            if test_mode:
                logger.info(f"[TEST] Would rename: {os.path.basename(pdf_path)} -> {new_name}")
            elif not os.path.isfile(new_path):
                os.rename(pdf_path, new_path)
                logger.info(f"Renamed: {os.path.basename(pdf_path)} -> {new_name}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error renaming Cybrary file {pdf_path}: {e}")
        return False

def extract_metadata(pdf_path, release_tag="certs", test_mode=False, github_page_url="https://lspironello.github.io", 
                    filter_skill=None, filter_year=None, filter_title=None, fetch_urls=False, data_dir=DATA_DIR, 
                    verbose=False, debug_dir=DEBUG_DIR, provider_id=None):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = ''.join(page.extract_text() or '' for page in pdf.pages)
        text = clean_text(text)
        
        if verbose:
            logger.info(f"Extracted text from {pdf_path}: {text[:200]}...")
        
        if debug_dir and not os.path.exists(debug_dir):
            os.makedirs(debug_dir)
        if debug_dir:
            debug_path = os.path.join(debug_dir, f"{os.path.basename(pdf_path)}.txt")
            with open(debug_path, 'w') as f:
                f.write(text)
        
        metadata = {}
 
        if provider_id == 'udemy':
            title_match = re.search(r'completed the (.+?)(?:online course)? on', text, re.IGNORECASE | re.DOTALL)
            date_match = re.search(r'on (.+?)(?:Instructor|Certificate|UC-)', text, re.IGNORECASE | re.DOTALL)
            cert_id_match = re.search(r'Certificate no\. (UC-[A-Z0-9]+)', text) or re.search(r'Certificate url ude\.my/(UC-[A-Z0-9]+)', text)
            instructor_match = re.search(r'Instructor: (.+?)(?:\n|$)', text, re.IGNORECASE)
            if title_match and date_match:
                title = title_match.group(1).strip()
                completion = date_match.group(1).strip()
                instructors = [instructor_match.group(1).strip()] if instructor_match else []
                cert_id = cert_id_match.group(1) if cert_id_match else generate_cert_id(pdf_path)[:8]
                skills = ''  # Udemy typically no skills
                year = re.search(r'(\d{4})', completion).group(1) if re.search(r'(\d{4})', completion) else None
                
                # Integrate renaming during metadata extraction
                pdf_path = rename_file(pdf_path, completion, title)
                
            else:
                if verbose:
                    logger.warning(f"No match for Udemy in {pdf_path}")
                return None
        elif provider_id == 'cybrary':
            title_match = re.search(r'provided by Cybrary in\s+(.+?)(?:\n|Date)', text, re.IGNORECASE | re.DOTALL)
            date_match = re.search(r'(May \d{1,2}, \d{4} \d{1,2}:\d{2}[AP]M UTC|\d{2}/\d{2}/\d{4})', text)
            cert_id_match = re.search(r'C-([a-f0-9]{8}-[a-f0-9]{6})', text)
            if title_match and date_match and cert_id_match:
                title = title_match.group(1).strip()
                completion = date_match.group(1).strip()
                instructors = []  # Not in text
                cert_id = cert_id_match.group(1)
                skills = ''
                year = re.search(r'(\d{4})', completion).group(1) if re.search(r'(\d{4})', completion) else None
            else:
                if verbose:
                    logger.warning(f"No match for Cybrary in {pdf_path}")
                return None
        elif provider_id == 'deeplearningai':
            title_match = re.search(r'congratulations on completing (.+?)(?=\.|!|\n)', text, re.IGNORECASE)
            filename_date_match = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(pdf_path))
            skills_match = re.findall(r'^([A-Z][a-zA-Z\s]+)$', text, re.MULTILINE)
            if title_match and filename_date_match:
                title = title_match.group(1).strip()
                completion = filename_date_match.group(1)
                instructors = ['DeepLearning.AI']
                cert_id = generate_cert_id(pdf_path)[:8]
                skills = ', '.join([s.strip() for s in skills_match if s.strip() and len(s.strip()) > 2])
                year = completion[:4]
            else:
                if verbose:
                    logger.warning(f"No match for DeepLearning.AI in {pdf_path}")
                return None
        elif provider_id == 'linkedinlearning':
            title_match = re.search(r'CertificateOfCompletion_(.*?)(?:_|\.pdf)', os.path.basename(pdf_path), re.IGNORECASE)
            date_match = re.search(r'completed by (\w+\s+\d{1,2},\s+\d{4})', text)
            skills_match = re.search(r'Top skills covered\s+(.+?)(?:Certificate ID|\n|$)', text, re.DOTALL)
            if title_match and date_match:
                title = title_match.group(1).strip().replace('_', ' ')
                completion = date_match.group(1).strip()
                instructors = []
                cert_id = re.search(r'Certificate ID:\s*(\w+)', text).group(1) if re.search(r'Certificate ID:\s*(\w+)', text) else generate_cert_id(pdf_path)[:8]
                skills = skills_match.group(1).strip() if skills_match and skills_match.group(1) else ''
                year = re.search(r'(\d{4})', completion).group(1) if re.search(r'(\d{4})', completion) else None
            else:
                if verbose:
                    logger.warning(f"No match for LinkedIn Learning in {pdf_path}")
                return None
        else:
            return None
        
        if (filter_skill and not re.search(filter_skill, skills, re.IGNORECASE)) or \
           (filter_year and year != filter_year) or \
           (filter_title and not re.search(filter_title, title, re.IGNORECASE)):
            return None
        
        course_url = generate_course_url(title, provider_id) if fetch_urls else ""
        pdf_filename = quote(os.path.basename(pdf_path))
        pdf_url = f"{github_page_url}/assets/pdfs/{pdf_filename}" if test_mode else f"https://github.com/{repo}/releases/download/{release_tag}/{pdf_filename}"
        
        metadata = {
            'title': title, 'completion': completion, 'skills': skills, 'year': year,
            'cert_id': cert_id, 'instructors': instructors, 'cert_url': '',
            'course_url': course_url, 'pdf_url': pdf_url, 'provider': provider_id
        }
        return metadata
    except Exception as e:
        logger.error(f"Error extracting metadata from {pdf_path}: {e}")
        return None

def generate_statistics(metadata_list):
    if not metadata_list:
        return {}
    stats = {
        'total_courses': len(metadata_list),
        'by_year': {},
        'by_provider': Counter(m['provider'] for m in metadata_list),
        'avg_skills_per_course': sum(1 for m in metadata_list if m['skills']) / len(metadata_list) if metadata_list else 0,
        'unique_skills': len(set(skill for m in metadata_list for skill in m['skills'].split(', ') if m['skills'])),
        'most_common_skill': Counter(skill for m in metadata_list for skill in m['skills'].split(', ') if m['skills']).most_common(1)[0][0] if any(m['skills'] for m in metadata_list) else 'N/A',
        'completion_trend': {m['year']: sum(1 for x in metadata_list if x['year'] == m['year']) for m in metadata_list}
    }
    for m in metadata_list:
        stats['by_year'][m['year']] = stats['by_year'].get(m['year'], 0) + 1
    return stats

def export_skills(metadata_list, output_format, data_dir, dry_run=False):
    if not metadata_list:
        return
    skills = set(skill for m in metadata_list for skill in m['skills'].split(', ') if m['skills'])
    output_path = os.path.join(data_dir, config.get('output_files', {}).get(f'skills_{output_format}', f'skills.{output_format}'))
    if dry_run:
        logger.info(f"[TEST] Would export skills to {output_path}")
        return
    if output_format == 'csv':
        with open(output_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Skill'])
            for skill in skills:
                writer.writerow([skill])
    else:  # text
        with open(output_path, 'w') as f:
            f.write('\n'.join(skills))
    logger.info(f"Exported skills to {output_path}")

def export_courses(metadata_list, output_format, data_dir, dry_run=False):
    if not metadata_list:
        return
    output_path = os.path.join(data_dir, config.get('output_files', {}).get(f'courses_{output_format}', f'courses.{output_format}'))
    if dry_run:
        logger.info(f"[TEST] Would export courses to {output_path}")
        return
    if output_format == 'csv':
        with open(output_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Title', 'Provider', 'Year'])
            for m in metadata_list:
                writer.writerow([m['title'], m['provider'], m['year']])
    else:  # text
        with open(output_path, 'w') as f:
            f.write('\n'.join(f"{m['title']} ({m['provider']}, {m['year']})" for m in metadata_list))
    logger.info(f"Exported courses to {output_path}")

def export_course_urls(metadata_list, data_dir, dry_run=False):
    if not metadata_list:
        return
    output_path = os.path.join(data_dir, config.get('output_files', {}).get('course_urls_csv', 'course_urls.csv'))
    if dry_run:
        logger.info(f"[TEST] Would export course URLs to {output_path}")
        return
    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Title', 'Course URL'])
        for m in metadata_list:
            if m['course_url']:
                writer.writerow([m['title'], m['course_url']])
    logger.info(f"Exported course URLs to {output_path}")

def display_file_contents(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            print(f"\nContents of {file_path}:")
            print(f.read())
    else:
        print(f"File {file_path} not found.")

# Load config and providers
args = parse_args()
config_path = args.config
if os.path.exists(config_path):
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
        BASE_DIR = config.get('base_dir', BASE_DIR)
        DATA_DIR = config.get('data_dir', DATA_DIR)
        DEBUG_DIR = config.get('debug_dir', DEBUG_DIR)
        ASSETS_DIR = config.get('assets_dir', ASSETS_DIR)
        repo = config.get('repo', 'lspironello/lspironello.github.io')
        release_tag = config.get('release_tag', 'certs')
        github_page_url = config.get('github_page_url', 'https://lspironello.github.io')
        providers_yaml = config.get('providers_yaml', os.path.join(DATA_DIR, 'providers.yml'))
    with open(providers_yaml, 'r') as f:
        providers = yaml.safe_load(f)
else:
    providers = [
        {'provider': 'udemy', 'subdirectory': 'udemy'},
        {'provider': 'cybrary', 'subdirectory': 'cybrary'},
        {'provider': 'deeplearningai', 'subdirectory': 'deeplearningai'},
        {'provider': 'linkedinlearning', 'subdirectory': 'linkedinlearning'}
    ]

if args.display_config and os.path.exists(config_path):
    display_file_contents(config_path)
elif not args.dry_run:
    import subprocess
    result = subprocess.run(['gh', 'release', 'view', release_tag, '--repo', repo], capture_output=True, text=True)
    if result.returncode != 0:
        os.system(f"gh release create {release_tag} --title 'Certificates' --notes 'All Certificates' --repo {repo}")

all_metadata = {}
for provider in providers:
    if args.provider and provider['provider'] != args.provider:
        continue
    provider_id = provider['provider']
    subdir = provider['subdirectory']
    provider_dir = os.path.join(BASE_DIR, subdir)
    
    if provider_id == 'udemy' and args.rename_udemy:
        for root, _, files in os.walk(provider_dir):
            for file in files:
                if file.endswith('.pdf'):
                    rename_udemy_file(os.path.join(root, file), test_mode=args.dry_run)
    elif provider_id == 'cybrary' and args.rename_cybrary:
        for root, _, files in os.walk(provider_dir):
            for file in files:
                if file.endswith('.pdf'):
                    rename_cybrary_file(os.path.join(root, file), test_mode=args.dry_run)
    
    metadata_list = []
    file_count = 0
    for root, _, files in os.walk(provider_dir):
        for file in files:
            if file.endswith('.pdf'):
                pdf_path = os.path.join(root, file)
                metadata = extract_metadata(
                    pdf_path, release_tag, args.test > 0, github_page_url,
                    args.filter_skill, args.filter_year, args.filter_title,
                    args.fetch_urls, DATA_DIR, args.verbose, DEBUG_DIR, provider_id
                )
                if metadata:
                    metadata_list.append(metadata)
                    if not args.dry_run:
                        if not args.test:
                            os.system(f"gh release upload {release_tag} \"{pdf_path}\" --repo {repo} --clobber")
                        else:
                            os.makedirs(ASSETS_DIR, exist_ok=True)
                            os.system(f"cp \"{pdf_path}\" {ASSETS_DIR}")
                file_count += 1
                if args.test > 0 and file_count >= args.test:
                    break
        if args.test > 0 and file_count >= args.test:
            break
    
    if metadata_list:
        json_path = os.path.join(DATA_DIR, config.get('output_files', {}).get(f'{provider_id}_certs_json', f'{provider_id}_certs.json'))
        if not args.dry_run:
            with open(json_path, 'w') as f:
                json.dump(metadata_list, f, indent=4)
        logger.info(f"Metadata extracted for {len(metadata_list)} PDFs in {provider_id}")
        all_metadata[provider_id] = metadata_list
    else:
        logger.info(f"No valid PDFs processed for {provider_id}")
    
    if args.generate_stats:
        stats = generate_statistics(metadata_list)
        stats_path = os.path.join(DATA_DIR, f"{provider_id}_stats.yml")
        if not args.dry_run:
            with open(stats_path, 'w') as f:
                yaml.dump(stats, f)
        if args.display_files:
            display_file_contents(stats_path)
    
    if args.generate_skills or args.output_skills or args.output_courses or args.output_urls:
        if args.generate_skills:
            skills = {skill for m in metadata_list for skill in m['skills'].split(', ') if m['skills']}
            skills_path = os.path.join(DATA_DIR, config.get('output_files', {}).get('skills_yml', 'skills.yml'))
            if not args.dry_run:
                with open(skills_path, 'w') as f:
                    yaml.dump({'skills': list(skills)}, f)
            if args.display_files:
                display_file_contents(skills_path)
        if args.output_skills:
            export_skills(metadata_list, args.output_skills, DATA_DIR, args.dry_run)
        if args.output_courses:
            export_courses(metadata_list, args.output_courses, DATA_DIR, args.dry_run)
        if args.output_urls:
            export_course_urls(metadata_list, DATA_DIR, args.dry_run)

if not args.dry_run:
    os.system(f"cd {DATA_DIR} && git add *.json *.yml *.csv *.txt && git commit -m 'Update certificate data for {args.provider or 'all providers'}' && git push origin master")
    
    
    
