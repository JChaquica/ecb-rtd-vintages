# Vintages of the ECB Real Time Database, 2001–2026

Code that rebuilds every vintage of the 278 series in the ECB's euro area Real Time Database
(RTD), from the vintage of 3 January 2001 to that of 9 September 2026, 261 vintages in all, and
the checks that show the result is right.

After 2014 the ECB does not publish these vintages as files. Its web service returns, for each
series, a log of every value published, revised or removed since 2001, and the vintages are
rebuilt from those logs. For 2001–2014 the ECB's own vintage files still exist, and wherever they
have data the rebuilt vintages are identical to them, in all 42,132 comparisons of a series with
one month's file.

How the vintages were built, what we found on the way and what we suggest are in the experience
report, [docs/experience_report.pdf](docs/experience_report.pdf).

The repository holds the code, the checks and their results, but not the data (1.3 GB in all).
`get_data.py` downloads from the ECB only the series you ask for, most often a few such as GDP and
inflation, and builds the vintages you ask for in the form you ask for. Nothing else is needed.

## Contents

* [Getting the data](#getting-the-data)
* [Before using a vintage: many series are not kept up to date](#before-using-a-vintage-many-series-are-not-kept-up-to-date)
* [What you get](#what-you-get)
* [Data sources and terms of use](#data-sources-and-terms-of-use)
* [How the vintages are rebuilt](#how-the-vintages-are-rebuilt)
* [Checks](#checks)
* [Requirements](#requirements)
* [Layout of the repository](#layout-of-the-repository)
* [Maintaining the repository](#maintaining-the-repository)
* [Licence and citation](#licence-and-citation)

## Getting the data

With Python 3.12 or later:

```
git clone https://github.com/JChaquica/ecb-rtd-vintages.git
cd ecb-rtd-vintages
python -m pip install -r requirements.txt
python get_data.py
```

With no options, `get_data.py` asks four questions in the terminal:

1. **Which series.** Type words to search the 278 series (`real gdp`, `hicp`, `unemployment
   rate`), then the numbers of the ones you want (`57`, `57 21`, `55-59`). `list` shows all 278 and
   `all` takes every one.
2. **Which form.** `raw` (the ECB's logs as downloaded), `tables` (one table per series, periods
   by vintage dates) or `workbooks` (one Excel file per series per vintage), or several of them.
3. **Which vintages.** A year or a range of years of the vintage dates (`2015`, `2008-2012`,
   `2020-`), `latest` for the newest only, or all of them.
4. **Which observations.** A year or a range of years of the periods themselves, or all.

It then says what it will download and write, prints the command that does the same without the
questions, and asks before it starts. A session that takes real GDP and HICP inflation, as
workbooks, for the vintages of 2008 to 2012:

```
series> real gdp
    57  GDP / Real GDP  (quarterly)
    58  GDP / Real GDP - Japan  (quarterly)
    59  GDP / Real GDP - United States  (quarterly)
    64  GDP expenditure components / Exports of goods and services, real  (quarterly)
    67  GDP expenditure components / Government consumption, real  (quarterly)
    70  GDP expenditure components / Imports of goods and services, real  (quarterly)
    73  GDP expenditure components / Investment, real  (quarterly)
    76  GDP expenditure components / Private consumption, real  (quarterly)
   type the numbers of the ones you want
series> 57
   chosen (1): Real GDP
series> inflation
    20  Consumer prices (HICP) / HICP inflation rate, all items  (monthly)
   type the numbers of the ones you want
series> 20
   chosen (2): Real GDP; HICP inflation rate, all items
series>
form> 3
years> 2008-2012
years>

Ready:
  series        2: Real GDP; HICP inflation rate, all items
  form          workbooks
  vintages      2008 to 2012 (60 vintage dates)
  observations  all
  downloads     2 logs from the ECB, about 1-3 minutes
  writes        up to 120 workbooks, about 1.4 MB, in data/processed/

The same without the questions:
  python get_data.py --series Q.S0.S.G_GDPM_TO_C.E M.S0.N.P_C_OV.A --form workbooks --vintages=2008-2012

Go ahead? [Y/n]
```

The same choices can be given as options, which is the way to script a download or to record
exactly what was taken:

```
python get_data.py --list                                     # the 278 series and their numbers
python get_data.py --series "Real GDP" 21                     # by name or number: every vintage, tables and workbooks
python get_data.py --series GDP --vintages 2008-2012          # an indicator name takes every series in it
python get_data.py --series Q.S0.S.G_GDPM_TO_C.E --form tables --vintages latest --observations 2000-
python get_data.py --all                                      # everything
```

`--refresh` downloads again logs that are already on disk, to add the vintages the ECB has
published since. `python get_data.py --help` lists every option.

**Without typing commands (Windows).** Download the repository (on GitHub: *Code*, then
*Download ZIP*), unzip it and double-click `get_data.bat`. It asks the same four questions in a
window. The first time, it installs the packages the code needs into a `.venv` folder beside it,
which takes a minute or two. It needs Python 3.12 or later, from
[python.org](https://www.python.org/downloads/).

**How long it takes.** The ECB's server answers in seconds for a series it has been asked for
recently and takes one to three minutes for one it has not; `get_data.py` sends up to four
requests at a time. Real GDP and HICP inflation, downloaded into an empty copy of the repository
and built into every table and workbook, took 18 seconds. Building takes seconds for a few series;
every table of all 278 takes about two minutes, and every workbook of all 278 about an hour. A log
already on disk is not downloaded again unless `--refresh` asks for it.

**Where to put the repository on Windows.** Excel cannot open a workbook whose full path is longer
than 218 characters. The longest path inside the repository is 134 characters, so the folder that
holds it should be at most 83 characters long, such as `C:\data\ecb-rtd-vintages`.
`get_data.py` checks this before it downloads anything and says so if a workbook you asked for
would be too deep. A folder that cannot move can be given a short drive letter instead:
`subst R: "C:\long\path\to\ecb-rtd-vintages"`, then run `python get_data.py` from `R:\`.

## Before using a vintage: many series are not kept up to date

The vintages reproduce the ECB's database, and the ECB has not kept every series up to date. For
the series and vintages below, a vintage does not show what was known on its date.

* In the vintage of 9 September 2026, 107 of the 278 series end at least ten months earlier than
  they normally would. Of these, 72 were last updated on 4 June 2025 (among them most of the money,
  survey and balance of payments series) and 18 on 29 October 2025 (industrial production,
  producer prices and retail sales). Use these series only up to the vintage at which each one
  stopped.
* Nine series have no observations at all in any vintage from January 2015 to March 2024 (five
  government bond yields, two raw material price series, two producer price series).
* Twenty-one survey series have no observations in the four vintages from February to June 2023.
* The 10 euro area unemployment series, the 14 employment series, the 6 exchange rate series and
  construction prices were behind for between 9 and 25 vintages in a row, up to the vintage of
  4 June 2025. They are up to date now, but those vintages do not show what was known at the time.
* In 296 cases in 2001–2014, covering 20 series, a rebuilt vintage holds values that the ECB's own
  file for that month leaves blank. Most are the five government bond yields from February 2008 to
  March 2011, which the ECB had stopped publishing but did not remove from its log until 6 April
  2011.
* For M1, M2, M3, loans to the private sector and credit to the private sector, the ECB's
  adjustment code is the wrong way round: the series it codes `N` (not seasonally adjusted) holds
  the seasonally adjusted figures, and the one it codes `Y` the unadjusted ones. The repository
  names and labels these ten series by what their figures are, and the note on each gives the
  ECB's code. (M3 annual growth is coded correctly.)

In all, 153 of the 278 series were behind or had no observations in at least two consecutive
vintages since 2015.

`get_data.py` says, at the end of every run, which of the series you took were behind in the
vintages you took, and writes every such stretch to `data/processed/not_up_to_date.csv`. For all
278 series:

* [output/series_status.csv](output/series_status.csv) says, in one line per series, whether it
  is current or has stopped in the latest vintage.
* [output/series_last_update.csv](output/series_last_update.csv) lists the last update of every
  series and how far behind it is.
* [output/series_gaps.csv](output/series_gaps.csv) lists, for each series, the vintages in which
  it is behind or has no observations.
* [output/series_status_by_vintage.csv](output/series_status_by_vintage.csv) gives the status of
  every series in every vintage (current, behind, no observations, not yet published).
* [output/old_file_check.csv](output/old_file_check.csv) lists the 296 cases. They are the rows
  whose status is "old file column empty, rebuilt vintage has data".

A series counts as behind in a vintage when either of two things is true. Its last observation is
older than was usual for that series in 2015–2022, by at least three months (monthly series), two
quarters (quarterly) or one year (annual). Or the ECB has left the series unchanged for more
vintages in a row than it ever did in those years. The thresholds are a judgement. Halving or
doubling them leaves the same 107 series behind in the latest vintage.
[code/series_status.py](code/series_status.py) gives the details.

## What you get

| Form | Where | What |
|---|---|---|
| raw | `data/raw/history/<KEY>.csv` | The ECB's log of the series, exactly as its server sent it: every value published, revised or removed, with its time stamp. Always kept, whatever form you choose, since the others are built from it. `manifest.csv` beside the logs records when each was downloaded and its SHA-256. |
| tables | `data/processed/raw_vintages/<KEY>.csv` | One table per series: periods in rows, vintage dates in columns. The easier form for studying revisions. |
| workbooks | `data/processed/vintages/<Indicator>/<Variable>/vintage_<DATE>.xlsx` | One workbook per series per vintage, in the layout of ALFRED, the St. Louis Fed's archive of vintages. The first sheet describes the series (the ECB's title, series key, units, frequency and adjustment), the second holds the first day of each period and the value. |

For example, `data/processed/vintages/GDP/Real GDP/vintage_2008-06-04.xlsx` is euro area real
GDP as the ECB had it on 4 June 2008. A series has a workbook only for the vintages in which it
has observations. The first vintage holds 183 of the 278 series; another 45 start later in 2001,
26 in 2002 or 2003 and 24 in 2012.

```python
import pandas as pd

# Real GDP as known on 4 June 2008 (the second sheet holds the data).
gdp = pd.read_excel("data/processed/vintages/GDP/Real GDP/vintage_2008-06-04.xlsx",
                    sheet_name=1, index_col=0)

# How real GDP for 2007 Q4 was revised over time.
raw = pd.read_csv("data/processed/raw_vintages/RTD.Q.S0.S.G_GDPM_TO_C.E.csv", index_col=0)
raw.loc["2007-Q4"]
```

Two files say what `data/processed/` holds. Each run replaces the forms of the series it builds
and leaves other series alone, so several runs add up.

* `data/processed/contents.csv` has a row per series and form: the vintages and observations
  taken, how many files, when the log was downloaded and whether the series is the same as in the
  published build (below).
* `data/processed/not_up_to_date.csv` has a row per stretch of vintages in which a series held
  there was behind or had no observations.

The repository itself keeps the files that describe the data: the list of the 278 series with
their names, units and ECB keys ([data/processed/variable_list.csv](data/processed/variable_list.csv)),
the 261 vintage dates ([data/processed/vintage_dates.csv](data/processed/vintage_dates.csv)) and
the fingerprints of the published build ([data/processed/checksums.csv](data/processed/checksums.csv)).

**The same numbers on any machine.** A log dates every change, so a series downloaded later
rebuilds to the same vintages, up to 9 September 2026, as the one downloaded on 3 October 2026
from which this repository was built, unless the ECB has since rewritten its past record.
`checksums.csv` holds a SHA-256 of every series' vintages up to that date, and `get_data.py`
compares each series it builds with it and says whether they are the same. A series the ECB had
rewritten would be reported as different, and its tables would show the ECB's record as it then
is.

**Vintages published after 9 September 2026.** Vintage dates are the days on which any of the 278
series changed. A run with every series finds all of them. A run with a few series finds those
on which one of its series changed, and says so: a vintage on which none of them changed is
missing from its tables, but would equal the vintage before it.

## Data sources and terms of use

| Data | Source | In the repository? |
|---|---|---|
| The logs of the 278 RTD series | ECB Data Portal API, `https://data-api.ecb.europa.eu/service/data/RTD/<key>?includeHistory=true`; the published build used logs downloaded on 3 October 2026 | No. `get_data.py` downloads them. |
| The list of RTD series and its attributes | Same API, `RTD?detail=nodata`, 3 October 2026 | Yes: [data/raw/series_metadata.csv](data/raw/series_metadata.csv) |
| The ECB's code lists for the RTD | `https://data-api.ecb.europa.eu/service/dataflow/ECB/RTD?references=all`, 3 October 2026 | Yes: [data/raw/rtd_structure.xml](data/raw/rtd_structure.xml) |
| The ECB's vintage files for 2001–2014 and the pre-2001 file | RTD page of the ECB Data Portal, `https://data.ecb.europa.eu/sites/default/files/2024-08/{monthly,quarterly,annual,rtdb38}.zip` | No (36 MB). Checks 03 and 04 download them. |
| Eurostat's quarterly national accounts, nine series | Eurostat API, 5 October 2026 | Yes: [data/raw/eurostat/](data/raw/eurostat/), with a manifest of addresses, times and SHA-256 |
| The ECB's calendar of monetary policy decisions, 2001–2026 | `https://www.ecb.europa.eu/press/govcdec/mopo/<year>/`, 5 October 2026 | Yes: [data/raw/ecb_calendar/](data/raw/ecb_calendar/), with a manifest |
| Growth rates of euro area GDP from 28 Eurostat releases | Eurostat's euro indicators releases, read and typed in by hand; each row gives the release's address | Yes: [data/raw/eurostat_release_lookups.csv](data/raw/eurostat_release_lookups.csv) |

**Terms of use.** The RTD is published by the European Central Bank. Under the ECB's
[policy regarding the reuse of ESCB statistics](https://www.ecb.europa.eu/stats/ecb_statistics/governance_and_quality_framework/html/usage_policy.en.html),
its statistics may be reused free of charge provided the source is quoted ("Source: ECB
statistics") and the statistics and their metadata are not modified. The code here changes no
value: the tables and workbooks hold the ECB's values as the ECB published them, arranged by
vintage date, with the ECB's own titles and descriptions. The short names, and the notes where an
ECB code is wrong, are the repository's. Eurostat's data may be reused provided the source is
acknowledged ([Eurostat copyright notice](https://ec.europa.eu/eurostat/help/copyright-notice)).

## How the vintages are rebuilt

Each series' log has a row for every value the ECB published (`Replace`, dated by `VALID_FROM`)
and every period it removed (`Delete`, dated by `VALID_TO`). The vintage of a series on date *v*
is, for each period, the latest of its rows dated on or before *v*: a value if that row published
one, nothing if it removed the period, and nothing if the period has no row yet. "Latest" is
decided by the full time stamp. That matters on two days, 20 January and 8 September 2021, when
the ECB re-sent most series by removing every observation at 15:30:00 and entering it again at
15:30:01; comparing only the day would empty those vintages.

The vintage dates are the days on which any series changed: the freeze days before each meeting of
the Governing Council. Every table has a column for each, so a series that did not change on a
date repeats the previous column.

Values are read with Python's own conversion from text, so each is exactly the number the ECB
published. (pandas' faster default is off in the last binary digit for about 13,000 of the
4.1 million values, those with 16 or 17 significant digits.)

The ECB's server sometimes cuts a response short and still reports success, and keeps serving the
cut copy from its cache; on 3 October 2026, 13 of the 278 series came back cut at least once. A
log is therefore kept only if two separate downloads are identical byte for byte and every row is
whole, and is downloaded again after two, four and six minutes otherwise. Each series is
requested on its own, because a request for several series at once (a wildcard key) comes back
without the removals.

The docstrings of [code/download.py](code/download.py), [code/vintages.py](code/vintages.py),
[code/workbooks.py](code/workbooks.py) and [code/series_status.py](code/series_status.py) give the
details.

## Checks

Nine checks test the result against sources outside the rebuild. Each writes what it found to
`output/` and its log to `output/logs/`, and stops with exit code 1 if it finds a problem.
`python checks/run_all.py` runs them all in order, in about four minutes; `--offline` leaves out
06, the one that needs the ECB's server. The results below are those of 7 October 2026, except
06, last run on 5 October.

| Check | What it tests | Result for the published build |
|---|---|---|
| [01_logs.py](checks/01_logs.py) | The logs: that their time stamps decide the order of every period's rows, and that their dates are the 261 vintage dates | 34,989 same-day removals and new values, all on 20 January and 8 September 2021, the new value stamped one second later in every one; no undecided order; the 261 dates match |
| [02_series_status.py](checks/02_series_status.py) | Which series the ECB still updates (above) | 171 series up to date in the latest vintage, 107 behind |
| [03_old_files.py](checks/03_old_files.py) | The vintages of 2001–2014 against the ECB's own vintage files, file by file and cell by cell | All 42,132 comparisons identical, 6,616,172 values; 296 where the old file is empty and the rebuild is not (above). 44 columns of the old files, 29 of them for the United States or Japan, have no series in the web service |
| [04_pre2001_file.py](checks/04_pre2001_file.py) | What the ECB's file of vintages before 2001 holds | 38 workbooks with vintage columns; only producer prices (`IPP.xls`) has vintages before 2001, from October 1999 |
| [05_workbooks.py](checks/05_workbooks.py) | 556 workbooks reopened and compared with the tables | Every date agrees; no value differs by more than Excel's 15 significant digits allow |
| [06_current_data.py](checks/06_current_data.py) | The newest vintage against the data the ECB serves today, which catches a log cut short at the end of a line | All 80,731 values agree to 14 significant digits |
| [07_eurostat.py](checks/07_eurostat.py) | The newest vintage of nine national accounts series against Eurostat | 9 of 9 agree |
| [08_vintage_dates.py](checks/08_vintage_dates.py) | The vintage dates against the ECB's calendar of monetary policy decisions | From 2015 every vintage is on the working day before a decision, and every decision but one has a vintage (none on 23 July 2026, when no series changed) |
| [09_eurostat_releases.py](checks/09_eurostat_releases.py) | Chosen past vintages of real GDP against Eurostat's releases as published | 28 of 28 agree at one decimal; in 3 the figure is from a release published the day after the vintage date |

Checks 02, 03, 05 and 06 need the full build (`python get_data.py --all`) and 01 every log; 07 and
09 need only the series they compare, and 04 and 08 nothing beyond the repository.

## Requirements

* **Python 3.12 or later** and the packages in [requirements.txt](requirements.txt), pinned to the
  versions the data were built and checked with: `python -m pip install -r requirements.txt`. The
  published build used Python 3.14.7 on Windows 11. The code uses only the standard library and
  those packages, and nothing specific to Windows.
* **Internet** to reach `data-api.ecb.europa.eu`. Nothing else is needed to get the data. The
  checks reach `data.ecb.europa.eu` once (checks 03 and 04) and `data-api.ecb.europa.eu` (check
  06); the other files they compare with are in the repository.
* **Disk**: about 1.3 GB for every form of every series (logs 0.4 GB, tables 0.1 GB, workbooks
  0.8 GB), a few megabytes for a few series.
* **Time**: see [Getting the data](#getting-the-data).

## Layout of the repository

```
get_data.py                  the one command: choose series, years and form; download and build
get_data.bat                 the same, by double-click on Windows
code/common.py               where everything is, and the list of series
code/download.py             downloads a series' log from the ECB and checks it is whole
code/vintages.py             turns a log into one column per vintage
code/workbooks.py            writes the workbooks, and gives the series their plain names
code/series_status.py        judges whether a series is up to date in each vintage
checks/                      nine checks of the result, and run_all.py
data/raw/                    what came from outside: the ECB's logs (downloaded), its list of
                             series and code lists, and the inputs of the checks
data/processed/              what get_data.py builds, and the files describing it
output/                      what the checks found; output/logs/ has each check's log
docs/experience_report.pdf   the report: how the vintages were built, and what we found
requirements.txt             the Python packages, at the versions used
pyproject.toml               settings for editors only: tells them the scripts import from code/
LICENSE                      the licence of the code
```

## Maintaining the repository

* **A new ECB release.** `python get_data.py --all --refresh` downloads every log again and
  rebuilds everything, adding the new vintages to `vintage_dates.csv`. Run the checks afterwards;
  06 should be run soon after the download, before the ECB's next release.
* **The fingerprints.** `checksums.csv` is written by a run with every series when it is missing,
  and is never overwritten. To publish a new build, delete it and run `python get_data.py --all`.
* **New series.** `python get_data.py --update-series-list` downloads the ECB's list of RTD series
  and its code lists again and rewrites `variable_list.csv`. A series whose ECB concept code is
  new needs a plain name in [code/workbooks.py](code/workbooks.py) first; the script stops and
  names it otherwise.

## Licence and citation

The code is under the MIT licence ([LICENSE](LICENSE)). The data are the ECB's and Eurostat's, on
the terms above.

Source of the data: European Central Bank, euro area Real Time Database,
<https://data.ecb.europa.eu/data/datasets/RTD/data-information>. The database is described in
Giannone, D., Henry, J., Lalik, M. and Modugno, M. (2012), "An area-wide real-time database for
the euro area", *Review of Economics and Statistics* 94(4), 1000–1013 (also ECB Working Paper
1145, 2010).
