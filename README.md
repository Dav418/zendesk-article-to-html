# Zendesk Help Center -> Confluence Cloud Migrator

A reusable Python tool for migrating a Zendesk Help Center **category** into an **existing Confluence Cloud space**.

The migration keeps a local HTML/ZIP backup, recreates Zendesk sections as Confluence pages, uploads images and attachments, and rewrites links between migrated Zendesk articles so they point to their new Confluence pages.

> **Start here if you just want to get it running:** follow **Setup checklist**, **Zendesk credentials**, **Confluence credentials**, then **Run the migration** in that order. You do not need to understand the REST APIs.

---

## Setup checklist

Before running anything, collect these values.

| Value | Example | Where it comes from |
|---|---|---|
| Zendesk category URL | `https://company.zendesk.com/hc/en-gb/categories/123456-category-name` | Open the category you want to migrate and copy the browser URL |
| Zendesk email | `you@company.com` | Your normal Zendesk login email |
| Zendesk API token | secret | A Zendesk admin creates it; instructions below |
| Confluence base URL | `https://company.atlassian.net` | Your normal Confluence URL |
| Confluence email | `you@company.com` | Your normal Atlassian/Confluence account email |
| Confluence API token | secret | You normally create this yourself; instructions below |
| Confluence space key | `OPS` | Visible in the target space URL |
| Confluence parent page ID | `123456789` | Visible in the target page URL |

For the simplest one-off migration, the authentication used by this repo is:

```text
Zendesk:   your email + a Zendesk API token
Confluence: your email + an Atlassian API token
```

The repo also supports OAuth tokens, but you do **not** need OAuth if the simple token options below are available to you.

### Keep the secrets secret

- Never paste either API token into a ticket, chat, email, screenshot, or commit.
- Put secrets only in your local `.env` file.
- `.env` is already ignored by Git in this repo.
- The example values in `.env.example` are placeholders only.
- After the migration is finished and checked, revoke/delete the temporary tokens if they are no longer needed.

---

# 1. Install the repo

## macOS / Linux

Open Terminal in the repo folder and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

## Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

You will now have a local file called `.env`. That is the file you edit with the real URLs, emails and tokens.

---

# 2. Zendesk: get the source URL and credentials

## 2.1 Get the Zendesk category URL

Open the Zendesk Help Center category that contains the articles you want to migrate.

The URL should look like:

```text
https://company.zendesk.com/hc/en-gb/categories/123456-category-name
```

Copy the **whole URL** and put it in `.env`:

```env
ZENDESK_CATEGORY_URL=https://company.zendesk.com/hc/en-gb/categories/123456-category-name
```

You do not need to manually copy the category ID or locale. The script extracts both from the URL.

This migration is category-scoped: it fetches all articles returned for that category and follows Zendesk pagination automatically.

## 2.2 Important: Messaging keys are NOT the right credentials

If an existing application has values such as:

```text
ZENDESK_KEY_ID
ZENDESK_SECRET
```

or a Zendesk Messaging `channelKey` / `channelId`, **do not use them here**.

Those belong to Zendesk Messaging/end-user authentication. They do not authenticate this script to the Zendesk Help Center API.

## 2.3 Easiest option: Zendesk API token

For this option you need:

```text
Your normal Zendesk email
+
A Zendesk API token
```

### If you are NOT a Zendesk admin

You cannot create this token yourself. Ask a Zendesk admin to create one.

You can send them this exact message:

> Hi, I need to export our Zendesk Help Center content through the Zendesk Help Center API for a one-off Confluence migration. Could you please create a temporary Zendesk API token named **Zendesk Confluence migration** and share the token with me using our normal secure credential-sharing method? The script only reads Zendesk. I will authenticate as my own Zendesk user, so my existing Zendesk permissions still apply. The token can be deactivated/deleted after the migration.

The admin does **not** need to give you their Zendesk password or their own account credentials.

### Instructions for the Zendesk admin

1. Sign in to Zendesk as an administrator.
2. Open **Admin Center**.
3. Go to **Apps and integrations -> APIs -> API configuration**.
4. Make sure **Allow API token access** is enabled. If it is already enabled, leave it enabled.
5. Go to **Apps and integrations -> APIs -> API tokens**.
6. Click **Add API token**.
7. Enter a description such as:

   ```text
   Zendesk Confluence migration
   ```

8. Click **Save** to generate the token.
9. **Copy the full token immediately and store/share it securely.** Zendesk does not show the full token again after the token window is closed.
10. Do not send the token in an ordinary email/chat if your company has a password manager or other approved secret-sharing method.

Official Zendesk instructions:

- https://support.zendesk.com/hc/en-us/articles/8662004958746-Turning-on-and-off-API-access
- https://support.zendesk.com/hc/en-us/articles/4408889192858-Managing-API-token-access-to-the-Zendesk-API

### Put the Zendesk credentials in `.env`

Use **your own normal Zendesk login email**, not the admin's email:

```env
ZENDESK_OAUTH_TOKEN=
ZENDESK_EMAIL=you@company.com
ZENDESK_API_TOKEN=PASTE_THE_ZENDESK_TOKEN_HERE
```

Do **not** add `/token` to your email. The script does that internally.

Why is the email needed? A Zendesk API token is account-level rather than belonging to one user. Your email identifies which verified Zendesk user is making the request, so Zendesk applies that user's permissions.

### Zendesk API-token retirement

Zendesk is retiring this authentication method. As of October 2026:

- existing eligible accounts can still create API tokens until **27 October 2026**;
- after that date, new API-token creation is blocked;
- existing active API tokens are scheduled to stop working on **30 April 2027**.

That is fine for this short-lived migration, but do not build a permanent integration around a new API token.

Official announcement:

https://support.zendesk.com/hc/en-us/articles/10840968198042-Announcing-the-removal-of-API-tokens-as-an-authentication-method-for-API-requests

## 2.4 Zendesk OAuth alternative

If your company will not provide an API token, the repo can authenticate using a Zendesk OAuth access token instead:

```env
ZENDESK_OAUTH_TOKEN=PASTE_OAUTH_ACCESS_TOKEN_HERE
ZENDESK_EMAIL=
ZENDESK_API_TOKEN=
```

Creating a new Zendesk OAuth token normally requires an admin to create/configure an OAuth client first under:

**Admin Center -> Apps and integrations -> APIs -> OAuth clients**

This is a more involved setup than the temporary API-token route. If your organisation already has an OAuth client/token intended for this API access, use that. Do not reuse unrelated Messaging secrets.

Official Zendesk OAuth documentation:

https://support.zendesk.com/hc/en-us/articles/8889508417946-Managing-OAuth-token-access-to-the-API

---

# 3. Export Zendesk locally

At this point the Confluence settings can still be blank. The first command only reads Zendesk and writes files to your computer.

Run:

```bash
python main.py export
```

A successful export creates a workspace similar to:

```text
output/
└── zendesk-category-123456/
    ├── manifest.json
    ├── migration-report.csv
    ├── migration-report.json
    ├── Category Name-confluence-import.zip
    └── html/
        └── Category Name/
            ├── Category Name - Index.html
            ├── Section - Accounts.html
            ├── Article name.html
            └── Article name/
                ├── screenshot.png
                └── form.pdf
```

The ZIP is retained as a backup/alternative even if you use the direct Confluence uploader.

If `export` fails with `401 Unauthorized`, re-check the Zendesk email/token. If it fails with `403 Forbidden`, your Zendesk user may not have permission to read the requested content.

---

# 4. Confluence: get the target and credentials

You need an **existing Confluence Cloud space** and an **existing parent page** inside that space. The migration will be created underneath that parent page.

For the easiest setup this repo needs five Confluence values:

```text
1. Confluence base URL
2. Your Atlassian/Confluence email
3. Atlassian API token
4. Space key
5. Parent page ID
```

You normally do **not** need to be a Confluence admin. Your account does need permission to view the target space, create/edit pages below the target parent, and upload attachments.

## 4.1 Get the Confluence base URL

Open Confluence normally.

If a page looks like:

```text
https://company.atlassian.net/wiki/spaces/OPS/pages/123456789/Knowledge+Base
```

then your base URL is:

```env
CONFLUENCE_BASE_URL=https://company.atlassian.net
```

You can also paste a base URL ending in `/wiki`; the script normalises it. Using the plain site URL is simplest.

## 4.2 Get your Confluence email

Use the email address for the Atlassian account you normally use to sign in to Confluence:

```env
CONFLUENCE_EMAIL=you@company.com
```

This does not need to match the Zendesk email, although for many companies it will.

## 4.3 Create the Atlassian API token

Go directly to:

https://id.atlassian.com/manage-profile/security/api-tokens

Sign in with the same Atlassian account that can edit the target Confluence space.

Then:

1. Click **Create API token**.
2. For **this version of the repo, choose the normal `Create API token` option, not `Create API token with scopes`**.
3. Give it a clear name, for example:

   ```text
   Zendesk Confluence migration
   ```

4. Choose a short expiry. Around 30 days is sensible for a one-off migration.
5. Click **Create**.
6. Click **Copy to clipboard**.
7. Store the token securely. Atlassian will not show the full token again later.
8. Put it in `.env`:

   ```env
   CONFLUENCE_API_TOKEN=PASTE_THE_ATLASSIAN_TOKEN_HERE
   ```

Official Atlassian token page/documentation:

- https://id.atlassian.com/manage-profile/security/api-tokens
- https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account

### Why not choose a scoped token?

Atlassian recommends scoped tokens where possible. Scoped tokens use a different API hostname and require a Confluence Cloud ID. This version of the repo deliberately uses the simpler site-specific Confluence REST URL and therefore expects a **standard/unscoped Atlassian API token** when using email + token authentication.

If your organisation blocks standard API tokens and only permits scoped tokens, do not guess at the configuration. Use the OAuth option supported by the repo or update the client to support Atlassian's scoped-token API URL.

Official Confluence authentication documentation:

https://developer.atlassian.com/cloud/confluence/basic-auth-for-rest-apis/

## 4.4 Get the Confluence space key

Open the space where the migration should live.

A URL commonly looks like:

```text
https://company.atlassian.net/wiki/spaces/OPS/overview
```

or:

```text
https://company.atlassian.net/wiki/spaces/OPS/pages/123456789/Knowledge+Base
```

The value immediately after `/spaces/` is the **space key**.

For the examples above:

```env
CONFLUENCE_SPACE_KEY=OPS
```

Do not put the whole URL into `CONFLUENCE_SPACE_KEY`.

## 4.5 Choose the parent page and get its page ID

Open the existing Confluence page underneath which the migrated knowledge base should live.

For example:

```text
https://company.atlassian.net/wiki/spaces/OPS/pages/123456789/Knowledge+Base
```

The numeric value immediately after `/pages/` is the page ID:

```env
CONFLUENCE_PARENT_PAGE_ID=123456789
```

In this example the resulting structure will be approximately:

```text
Knowledge Base                         <- existing parent page 123456789
└── Zendesk category name             <- created by this tool
    ├── Section A
    │   ├── Article 1
    │   └── Article 2
    └── Section B
        └── Article 3
```

The tool does not replace or delete the existing parent page.

## 4.6 Put the Confluence values in `.env`

The normal setup is:

```env
CONFLUENCE_BASE_URL=https://company.atlassian.net

CONFLUENCE_OAUTH_TOKEN=
CONFLUENCE_EMAIL=you@company.com
CONFLUENCE_API_TOKEN=PASTE_THE_ATLASSIAN_TOKEN_HERE

CONFLUENCE_SPACE_KEY=OPS
CONFLUENCE_PARENT_PAGE_ID=123456789

CONFLUENCE_CREATE_CATEGORY_ROOT=true
CONFLUENCE_ROOT_PAGE_TITLE=
CONFLUENCE_EXISTING_TITLE_POLICY=fail
CONFLUENCE_UPLOAD_DRAFTS=false
CONFLUENCE_RESTRICTED_ARTICLE_POLICY=warn
```

Leave `CONFLUENCE_ROOT_PAGE_TITLE` blank unless you specifically want the new top-level migration page to have a different name. Blank means: use the real Zendesk category name.

### Confluence OAuth alternative

If you already have an appropriate Confluence OAuth bearer token, use:

```env
CONFLUENCE_OAUTH_TOKEN=PASTE_OAUTH_ACCESS_TOKEN_HERE
CONFLUENCE_EMAIL=
CONFLUENCE_API_TOKEN=
```

For the normal one-off migration, the email + standard Atlassian API token route is simpler.

---

# 5. Complete `.env` example

This is what a normal one-off migration configuration looks like. **Replace every example value with your own value.**

```env
# -----------------------------------------------------------------------------
# Zendesk source
# -----------------------------------------------------------------------------
ZENDESK_CATEGORY_URL=https://company.zendesk.com/hc/en-gb/categories/123456-category-name

ZENDESK_OAUTH_TOKEN=
ZENDESK_EMAIL=you@company.com
ZENDESK_API_TOKEN=PASTE_ZENDESK_TOKEN_HERE

# -----------------------------------------------------------------------------
# Local export
# -----------------------------------------------------------------------------
OUTPUT_DIR=output
INCLUDE_DRAFTS=true
ALLOW_PARTIAL_EXPORT=false
FAIL_ON_UNRESOLVED_ZENDESK_LINKS=false
REQUEST_TIMEOUT_SECONDS=30

# -----------------------------------------------------------------------------
# Confluence target
# -----------------------------------------------------------------------------
CONFLUENCE_BASE_URL=https://company.atlassian.net

CONFLUENCE_OAUTH_TOKEN=
CONFLUENCE_EMAIL=you@company.com
CONFLUENCE_API_TOKEN=PASTE_ATLASSIAN_TOKEN_HERE

CONFLUENCE_SPACE_KEY=OPS
CONFLUENCE_PARENT_PAGE_ID=123456789

CONFLUENCE_CREATE_CATEGORY_ROOT=true
CONFLUENCE_ROOT_PAGE_TITLE=
CONFLUENCE_EXISTING_TITLE_POLICY=fail
CONFLUENCE_UPLOAD_DRAFTS=false
CONFLUENCE_RESTRICTED_ARTICLE_POLICY=warn
```

Never commit the completed `.env` file.

---

# 6. Run the migration

The migration is deliberately split into three commands so you do not accidentally create hundreds of Confluence pages while merely testing credentials.

## Step 1 - Export everything from Zendesk

```bash
python main.py export
```

This command:

- reads Zendesk;
- exports every matching article returned for the selected category;
- downloads article attachments and inline images;
- records Zendesk sections/hierarchy;
- produces the local manifest/reports;
- creates a Confluence-compatible HTML ZIP backup;
- **does not write anything to Confluence**.

Check the summary and `migration-report.csv` before continuing.

## Step 2 - Run Confluence preflight

```bash
python main.py preflight
```

This is **read-only** against Confluence. It creates no pages and uploads no attachments.

It checks:

- whether your Confluence credentials work;
- whether the configured space exists;
- whether the parent page exists and belongs to that space;
- existing page-title conflicts;
- how many sections/articles are planned;
- draft handling;
- Zendesk restricted-article warnings;
- existing resume state from a previous interrupted migration.

The detailed result is saved to:

```text
output/zendesk-category-123456/confluence-preflight.json
```

Do not continue to upload until preflight succeeds and the target looks correct.

## Step 3 - Upload to the existing Confluence space

Only after preflight succeeds:

```bash
python main.py upload --yes
```

`--yes` is required deliberately because this command writes to Confluence.

The uploader then:

1. creates the category root page, if enabled;
2. recreates Zendesk section pages in parent/child order;
3. creates all article pages with temporary placeholder bodies;
4. records every new Confluence page ID/URL;
5. uploads each article's images/files as Confluence attachments;
6. rewrites migrated Zendesk article/section/category links to the new Confluence destinations;
7. updates the article bodies;
8. populates the section/index pages.

Creating the pages before rewriting the bodies is what allows article A to link correctly to article B even when article B did not exist in Confluence before the migration.

---

# 7. What the migration creates

With the recommended settings:

```text
Existing Confluence space
└── Existing parent page                    <- you choose this
    └── Zendesk category name               <- tool creates this
        ├── Section A
        │   ├── Article 1
        │   └── Article 2
        └── Section B
            ├── Nested section
            │   └── Article 3
            └── Article 4
```

If:

```env
CONFLUENCE_CREATE_CATEGORY_ROOT=false
```

then the section pages are created directly beneath your existing parent page. In that mode the script does not edit the existing parent page.

---

# 8. Link handling

For an original Zendesk link such as:

```text
https://company.zendesk.com/hc/en-gb/articles/987654-how-to-upload-a-document
```

if article `987654` is part of this migration, the final Confluence content points to the newly-created Confluence page instead of the old Zendesk page.

The tool handles:

- article -> migrated article;
- article -> migrated section;
- article -> migrated category;
- links in section/category descriptions;
- `#fragment` anchors;
- Zendesk attachment links;
- inline Zendesk images;
- externally hosted inline images successfully downloaded during export.

Normal external links are not changed.

If a Zendesk link points to an article outside the selected category, it is left unchanged and recorded in the migration report rather than guessed.

---

# 9. Images and attachments

Zendesk article attachments are downloaded during `export`.

During direct Confluence upload, each file is attached to the corresponding Confluence article page. Image elements are converted to Confluence attachment-image markup and ordinary attachment links are converted to Confluence attachment links.

The final Confluence pages therefore do not need Zendesk to remain online in order to display migrated images/files that were successfully exported.

---

# 10. Drafts and restricted Zendesk articles

## Drafts

Local export defaults to:

```env
INCLUDE_DRAFTS=true
```

so the backup contains drafts that your Zendesk account can access.

Direct Confluence upload defaults to:

```env
CONFLUENCE_UPLOAD_DRAFTS=false
```

because a Zendesk draft uploaded with the normal Confluence page API would become a normal published Confluence page.

If you explicitly set:

```env
CONFLUENCE_UPLOAD_DRAFTS=true
```

the draft is uploaded as a normal Confluence page with a visible `Zendesk status: Draft` marker.

## Restricted articles

Zendesk user-segment restrictions do not automatically translate into equivalent Confluence restrictions.

Default:

```env
CONFLUENCE_RESTRICTED_ARTICLE_POLICY=warn
```

The migration can continue, but preflight/reports warn you that restricted Zendesk pages will inherit the destination Confluence hierarchy's permissions.

For a stricter migration:

```env
CONFLUENCE_RESTRICTED_ARTICLE_POLICY=fail
```

Preflight then blocks the upload while restricted Zendesk articles are present.

---

# 11. Existing Confluence title conflicts

Confluence page titles need to be unique within the target space for the API flow used here.

Recommended/default:

```env
CONFLUENCE_EXISTING_TITLE_POLICY=fail
```

If a required title already exists elsewhere in the space, preflight fails **before anything is created**.

If you prefer automatic safe renaming:

```env
CONFLUENCE_EXISTING_TITLE_POLICY=suffix
```

For example:

```text
Opening an account
```

may become:

```text
Opening an account (Zendesk article-123456)
```

Duplicate titles inside the migration itself are also made unique when necessary.

---

# 12. Resume behaviour

Once an upload starts, the tool creates:

```text
output/zendesk-category-123456/confluence-upload-state.json
```

It records the Confluence page IDs, titles and URLs created by this migration. It contains **no API tokens**.

Do not casually delete this file after a partial upload. It is what lets a rerun continue using already-created pages instead of creating another copy of everything.

On rerun, the tool also checks that pages recorded in state still exist and have not been manually renamed. If they have changed, it stops rather than blindly overwriting that manual change.

The tool is deliberately non-destructive. If an article later disappears from Zendesk, an old Confluence page recorded in state is not automatically deleted.

There is intentionally no automated delete/rollback command.

---

# 13. Reports

After `export`:

```text
migration-report.csv
migration-report.json
manifest.json
```

After `preflight`:

```text
confluence-preflight.json
```

After `upload --yes`:

```text
confluence-upload-report.csv
confluence-upload-report.json
confluence-upload-state.json
```

The upload report includes the Zendesk article ID, original title, final Confluence title, Confluence page ID/URL, asset counts and link-rewrite counts.

---

# 14. Common problems

## Zendesk: `401 Unauthorized`

Check:

- `ZENDESK_EMAIL` is your real verified Zendesk login email;
- `ZENDESK_API_TOKEN` contains the token and has no accidental spaces;
- you did not put `/token` onto the email yourself;
- the token is still active;
- `ZENDESK_OAUTH_TOKEN` is blank when using email + API token.

## Zendesk: `403 Forbidden`

Authentication may be valid but your Zendesk user may not have permission to see the category/article. The API uses the permissions of the authenticated user.

## Zendesk: I only have `ZENDESK_KEY_ID` and `ZENDESK_SECRET`

Those are normally Zendesk Messaging credentials. They are not Help Center API credentials. Ask a Zendesk admin for the API/OAuth access described above.

## Zendesk: admin cannot see `Add API token`

Possible reasons include account policy or Zendesk's API-token retirement. Use Zendesk OAuth instead. From **27 October 2026**, Zendesk says new Support API-token creation is blocked for everyone.

## Confluence: `401 Unauthorized`

Check:

- `CONFLUENCE_EMAIL` is the Atlassian account that created the token;
- you copied the full token when it was created;
- the token has not expired/revoked;
- for this repo's email+token path you created the standard **Create API token** token, not the scoped-token variant;
- `CONFLUENCE_OAUTH_TOKEN` is blank when using email + API token.

## Confluence: `403 Forbidden`

Authentication can be correct while your Atlassian user lacks permission. Confirm your normal Confluence account can:

- view the target space;
- view the chosen parent page;
- create and edit pages there;
- upload attachments.

## Confluence: wrong space or parent page

Do **not** run `upload --yes`. Correct:

```env
CONFLUENCE_SPACE_KEY=...
CONFLUENCE_PARENT_PAGE_ID=...
```

and rerun:

```bash
python main.py preflight
```

Preflight is specifically there to catch this before the script creates pages.

---

# 15. After the migration

Once you have checked the Confluence pages, links and attachments:

1. keep the local export/reports somewhere appropriate for your migration records;
2. revoke/delete the temporary Atlassian API token if it is no longer needed;
3. ask the Zendesk admin to deactivate/delete the temporary Zendesk API token if it is no longer needed;
4. do not leave `.env` sitting in an insecure shared folder;
5. never commit `.env` to source control.

For Zendesk API tokens, an admin can manage them under:

**Admin Center -> Apps and integrations -> APIs -> API tokens**

For your Atlassian API tokens, manage/revoke them here:

https://id.atlassian.com/manage-profile/security/api-tokens

---

# 16. Tests

Install dev dependencies and run:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

---

# Relevant API documentation

Zendesk:

- Help Center articles: https://developer.zendesk.com/api-reference/help_center/help-center-api/articles/
- Sections: https://developer.zendesk.com/api-reference/help_center/help-center-api/sections/
- Article attachments: https://developer.zendesk.com/api-reference/help_center/help-center-api/article_attachments/
- Zendesk API authentication: https://support.zendesk.com/hc/en-us/articles/4408831452954-How-can-I-authenticate-API-requests
- Zendesk API-token management: https://support.zendesk.com/hc/en-us/articles/4408889192858-Managing-API-token-access-to-the-Zendesk-API
- Zendesk API-token retirement: https://support.zendesk.com/hc/en-us/articles/10840968198042-Announcing-the-removal-of-API-tokens-as-an-authentication-method-for-API-requests

Confluence / Atlassian:

- Atlassian API-token management: https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account
- Confluence basic auth: https://developer.atlassian.com/cloud/confluence/basic-auth-for-rest-apis/
- Confluence REST examples: https://developer.atlassian.com/cloud/confluence/rest-api-examples/
- Confluence attachment API: https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content---attachments/
