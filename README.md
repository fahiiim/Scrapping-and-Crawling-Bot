# VEVOR Product Scraper

Crawls public VEVOR category/listing pages, parses product data, and writes one deduplicated CSV.

## Install

```bash
python -m pip install -r requirements.txt
```

Optional: copy `.env.example` to `.env` and change the limits.

## Run

```bash
python -m app.main scrape
python -m app.main export
python -m app.main stats
python -m unittest discover -s tests -v
```

Use `--max-products N --max-pages N` for a small run. Zero means unlimited.

Output: `data/vevor_products.csv`

## Structure

```text
app/       crawler, parser, scraper, exporter, CLI, config
data/      final CSV
tests/     parser tests
```
