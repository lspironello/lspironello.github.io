source "https://rubygems.org"

# Use GitHub Pages gem (includes correct Jekyll version)
gem "github-pages", group: :jekyll_plugins

# Security updates
gem "webrick", ">= 1.8.2"
gem "rexml", ">= 3.3.9"

# Plugins
group :jekyll_plugins do
  gem "jekyll-feed", "~> 0.12"
  gem "jekyll-paginate"
end

# Windows and JRuby
platforms :mingw, :x64_mingw, :mswin, :jruby do
  gem "tzinfo", ">= 1", "< 3"
  gem "tzinfo-data"
end

gem "wdm", "~> 0.1", :platforms => [:mingw, :x64_mingw, :mswin]
gem "http_parser.rb", "~> 0.6.0", :platforms => [:jruby]
