# Zendesk Help Center -> Confluence HTML ZIP exporter

Exports every article in any Zendesk Help Center category (for one locale) into a ZIP suitable for Confluence Cloud's **HTML import** flow. The exporter is category-agnostic: point `ZENDESK_CATEGORY_URL` at whichever category you want to migrate.

The exporter:

- reads the category URL directly, e.g. `https://company.zendesk.com/hc/en-gb/categories/123456-category-name`
- fetches **all** articles in that category using Zendesk pagination
- fetches the category's sections and keeps the Zendesk ordering metadata
- exports article HTML
- downloads Zendesk article attachments
- downloads inline images, including externally-hosted images when accessible
- rewrites links to other articles that are part of the same export
- creates section index pages and adds the Zendesk section name to every article
- creates `migration-report.csv` and `migration-report.json`
- refuses to create the final ZIP when an article or asset failed, unless `ALLOW_PARTIAL_EXPORT=true`

## Important section limitation

Confluence Cloud's HTML ZIP importer documents a ZIP containing one folder of HTML files, with supplemental media in folders matching the page names. It does **not** document a way to recreate a Zendesk section -> article parent/child page tree from that ZIP.

This exporter therefore preserves sections in a safe ZIP-only way:

1. every Zendesk section gets a `Section - <name>` index page;
2. every article includes its Zendesk section at the top;
3. the CSV/JSON reports retain each article's `section_id` and `section_name`;
4. section/article ordering is retained in the generated index pages.

If the final Confluence space must have the articles physically nested under their section pages, that requires either manual re-parenting after import or a later Confluence REST API importer. This tool intentionally does not call Confluence because the requested output is ZIP-only.

## 1. Requirements

- Python 3.11+ (3.13 is fine)
- a Zendesk account that can see every article in the category that needs exporting
- either a Zendesk OAuth access token, or an API token if API-token authentication is enabled for your account

Zendesk filters Help Center API responses using the permissions of the authenticated user, so use an account that can see all required articles.

## 2. Set up

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and paste the category URL you want to export. The category name is read from Zendesk at runtime and is used for the output folder/ZIP unless you explicitly set `CONFLUENCE_SPACE_NAME`. Do not send the token to anyone.

### OAuth authentication (preferred)

```env
ZENDESK_CATEGORY_URL=https://company.zendesk.com/hc/en-gb/categories/123456-category-name
ZENDESK_OAUTH_TOKEN=your_oauth_access_token
ZENDESK_EMAIL=
ZENDESK_API_TOKEN=
```

### API-token authentication

```env
ZENDESK_CATEGORY_URL=https://company.zendesk.com/hc/en-gb/categories/123456-category-name
ZENDESK_OAUTH_TOKEN=
ZENDESK_EMAIL=you@company.com
ZENDESK_API_TOKEN=your_api_token
```

For Zendesk API-token authentication the username sent to Zendesk is automatically formed as `you@company.com/token`; you only enter your normal email in `.env`.

## 3. Run it

```bash
python main.py
```

The exporter does not write to Zendesk. It only performs GET requests.

## 4. Output

A successful run produces something like:

```text
output/
├── Category Name/
│   ├── Category Name - Index.html
│   ├── Section - Accounts.html
│   ├── How to open an account.html
│   ├── How to open an account/
│   │   ├── screenshot.png
│   │   └── form.pdf
│   └── ...
├── Category Name-confluence-import.zip
├── migration-report.csv
└── migration-report.json
```

The ZIP contains the space folder only. The migration reports are deliberately kept outside the ZIP.

## 5. Import to Confluence

In Confluence Cloud:

1. **Spaces**
2. **Import from other tools**
3. **HTML import**
4. upload the generated `output/<category name>-confluence-import.zip`
5. during the first test import, use **Only visible to me** / the most restrictive option available
6. review the imported content before changing the space permissions

## What is considered an error?

By default, the final ZIP is not created if:

- an article fails to export
- a Zendesk attachment fails to download
- an inline image fails to download

The reports are still written so you can see exactly what failed.

Links to Zendesk articles outside the selected category are warnings rather than hard failures. Set this if you want those to block the ZIP too:

```env
FAIL_ON_UNRESOLVED_ZENDESK_LINKS=true
```

To deliberately allow a partial ZIP despite failed article/assets downloads:

```env
ALLOW_PARTIAL_EXPORT=true
```

That is not recommended for the final migration.

## Notes about Confluence HTML import

The HTML importer supports normal content such as headings, paragraphs, links, images, tables, lists, quotes and inline code. Some richer HTML constructs are unsupported or simplified by Confluence.

Non-image Zendesk attachments are downloaded and linked from the bottom of the relevant article. They are included in the page's supplemental-media folder, but Confluence may ignore media types that its importer does not support. The migration report still proves that the file was retrieved from Zendesk.

Zendesk page-level visibility restrictions cannot be reproduced by a ZIP file. Restricted Zendesk articles are flagged in the report so their Confluence permissions can be reviewed after import.
