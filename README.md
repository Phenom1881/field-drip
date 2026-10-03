# Field Drip

Who outfits college football, tracked and sourced. Posts are Markdown files, deals live in three CSV files, and a GitHub Action rebuilds the whole site every time you commit: the home page, the tracker table, and a page for every school, conference, and brand.

## Put it on GitHub (one time)

1. Create a new public repository named `field-drip`. Leave "Add a README" unchecked.
2. Upload everything in this folder and keep the folder structure. On a Mac, Finder hides the `.github` folder, so press Command+Shift+Period to show it before you drag. If it still doesn't come through, use **Add file > Create new file**, type `.github/workflows/publish.yml` as the name, and paste in that file's contents.
3. In the repository, open **Settings > Pages** and set **Source** to **GitHub Actions**.
4. Open the **Actions** tab. "Build and publish the site" runs on every commit and takes about a minute. The site shows up at `https://phenom1881.github.io/field-drip/`.
5. Adding a custom domain later is one setting in **Settings > Pages**. Every link on the site adjusts on the next build.

## Write a post

Add a file to `posts/` named like `2026-10-03-short-title.md`. The part of the name after the date becomes the web address. Start it with this block, then write the story in Markdown underneath:

```
---
title: Ole Miss is leaving Nike for Adidas
dek: One or two sentences that sit under the headline.
date: 2026-10-03
kicker: Deal alert
schools: ole-miss, miami
thumb: Jul 1 / 2027
lead: true
---
```

| Line | What it does |
|---|---|
| `title` | Headline. Required. |
| `date` | Publish date as `2026-10-03`. Required. |
| `dek` | The summary under the headline and on story cards. |
| `kicker` | Small label above the headline: Deal alert, History, Deal file, Explainer. |
| `schools` | School ids from `data/schools.csv`, separated by commas. Conference and brand tags fill in on their own, so the post shows up under the right filters and hub pages. |
| `brands`, `conferences` | Extra tags for a post that isn't about one school, like `brands: Nike`. |
| `thumb` | Big text on the story card. A `/` starts a new line. |
| `thumb_style` | Card color: `field`, `green`, `ink`, or `gold`. |
| `lead: true` | Puts the post at the top of the home page. The newest lead post wins. |
| `draft: true` | Keeps the post off the site until you remove it. |

Every post is bylined to the `author` in `data/site.txt` (Field Drip Staff). Add an `author:` line to one post to change just that post.

The easiest way to post from a browser: open the `posts` folder on GitHub, choose **Add file > Create new file**, name it like `2026-10-07-school-brand.md`, paste the block above with your own lines, write the story under it, and choose **Commit changes**. The site rebuilds in about a minute.

**Photos.** Upload the image to `static/img/` (on GitHub: open that folder, **Add file > Upload files**). Then put it in the post on its own line, with an optional caption on the line right under it:

```
![Ole Miss in Nike, 2025](~/static/img/ole-miss-2025.jpg)
*Ole Miss wore Nike through the 2026 season. Photo: Your Name*
```

Keep photos under about 1 MB (an iPhone photo exported at a smaller size is fine), use lowercase names with hyphens, and only post photos you took or have permission to use. Team and press photos usually belong to the school or a news agency.

To link to another page on the site, start the link with `~/`, like `[Ole Miss](~/schools/ole-miss/)`. That keeps links working whether the site lives at `/field-drip/` or on its own domain.

## Tip line

Fill in `tips_form_url`, `tips_email`, or both in `data/site.txt`. Once either one is set, the site adds a Send a tip button in the header, a tip box on the home page, and a tips page. Leave both blank and none of it shows. Use an address made just for tips, since it's shown on the site.

## Update a deal

Deals live in `data/deals.csv`, one row per contract.

| Column | What goes in it |
|---|---|
| `id` | A short name for the deal, lowercase with hyphens: `ole-miss-adidas`. |
| `school` | The school's id from `data/schools.csv`. |
| `brand` | Nike, Adidas, Under Armour, New Balance, and so on. |
| `sub_brand` | Optional. Put `Jordan` here (with `Nike` as the brand) when the football team wears Jordan. The site lists Jordan as its own brand. |
| `start`, `end` | `2027-07-01`, `2027-07`, or just `2027` when that's all that's known. Leave blank if unknown. |
| `status` | One of `rumored`, `reported`, `agreed`, `official`, `in effect`, `ended`. |
| `value` | What's been reported about length and money, like `10 years, about $300M`. |
| `note` | One plain sentence that shows on the board and the deal card. |
| `updated` | The date you last checked the deal. It shows as "Checked" on the card. |

If a value contains a comma, wrap it in double quotes: `"10 years, about $300M"`. Lines that start with `#` are notes to yourself and are skipped.

When news moves a deal, change its row:

- **A new rumor or report**: add a row with status `reported` (or `rumored`), and add the story to `data/reports.csv`.
- **The school announces it**: change `reported` to `official` and update `updated`.
- **The new deal starts**: change the old deal to `ended` and the new one to `in effect`.

The home page board, the countdown, the scoreboard, and every school, conference, and brand page update on the next build.

## Add a source

Each row in `data/reports.csv` is one story or document about one deal: `deal` (the deal's id), `outlet`, `date`, `terms` (what that outlet reported), and `url`. When outlets disagree, add a row for each one. The deal card shows them side by side.

**No source, no publish.** A current or upcoming deal stays off the site until it has at least one row here. A school with no published deal stays off the site too. That lets you load a whole conference list at once: rows go into `data/deals.csv` right away, and each school appears the moment you add its source. The build log lists everything still waiting, which doubles as your to-do list. Ended deals don't need their own source, since the deal that replaced them carries it.

## Add a school

Add a row to `data/schools.csv`: an `id` (lowercase with hyphens, like `boise-state`), the `name` as it should appear, and the football `conference`. New conferences get their own page automatically.

## What the build checks

Before anything is published, the build checks every file. If something is wrong, the Actions run turns red, the log names the file and line, and the live site stays exactly as it was. It stops on:

- A status that isn't on the ladder, or a date it can't read
- A deal or source that points to a school or deal that doesn't exist
- A row with the wrong number of columns (usually a comma that needed quotes)
- Two deals for one school both marked `in effect`
- A post missing its title or date
- An em or en dash anywhere in a post or data file

It also prints notes that don't stop the build: the list of deals waiting for a source, and any start date that has passed while the status still says `reported`.

**iPhone tip:** Smart Punctuation turns two hyphens into an em dash, which stops the build. Turn it off in **Settings > General > Keyboard > Smart Punctuation**, or fix the line the log points to.

## Preview on your computer (optional)

```
pip install -r requirements.txt
python build.py
python -m http.server --directory _site 8000
```

Then open `http://localhost:8000`. Run `python build.py --check` to check the files without building.

## What's where

| Path | What it is |
|---|---|
| `posts/` | Stories, one Markdown file each |
| `pages/about.md` | The About page, including the status ladder definitions |
| `data/site.txt` | Site name, tagline, story byline, tip line, contact email |
| `data/schools.csv` | Schools and their conferences |
| `data/deals.csv` | Every apparel deal, past, current, and pending |
| `data/reports.csv` | Sources for each deal |
| `static/style.css` | The look: colors and fonts are at the top |
| `static/site.js` | Filters, sorting, the countdown, and the school finder |
| `build.py` | Checks everything and builds the site into `_site/` |
| `.github/workflows/publish.yml` | Runs the build and publishes on every commit |
