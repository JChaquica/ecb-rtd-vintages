# Vintages of the ECB Real Time Database, 2001–2026

Every vintage of the 278 series in the ECB's euro area Real Time Database (RTD), from the
vintage of 3 January 2001 to that of 9 September 2026, 261 vintages in all.

After 2014 the ECB does not publish these vintages as files. Its web service returns, for
each series, a log of every value published, revised or removed since 2001. The vintages here
were rebuilt from those logs, as downloaded on 3 October 2026. For 2001–2014 the ECB's own
vintage files still exist, and wherever they have data the rebuilt vintages are identical to
them, in all 42,132 comparisons of a series with one month's file.

The method, the checks and the experience of downloading the data are described in
[docs/rtd_vintages_report.pdf](docs/rtd_vintages_report.pdf).

This folder comes in two forms. The zip file holds everything, including the data. The GitHub
repository holds the scripts, the checks and the report but not the data, which come to 1.2 GB,
most of it in 67,532 workbooks, and are too much to keep in version control. Starting from the
repository, run the first four scripts to rebuild the data (a few hours, most of it waiting on
the ECB). Once the data are on disk, `python code/06_vintage_picker.py` opens a page for taking
a few series or a few dates out of them rather than all of it.

## Where to unzip

Unzip the package into a folder near the top of a drive, such as `C:\data`. Excel cannot open
a workbook whose full path is longer than 218 characters, and the longest path inside the
package is 151, so the path of the folder it is unzipped into should stay under 65 characters.
The same limit applies when the workbooks are rebuilt: `04_make_readable_vintages.py` stops
and says so if the folder it is run from is too deep.

## Before using a vintage: many series are not kept up to date

The vintages reproduce the ECB's database, and the ECB has not kept every series up to date.
For the series and vintages below, a vintage does not show what was known on its date.

* In the vintage of 9 September 2026, 107 of the 278 series end at least ten months earlier
  than they normally would. Of these, 72 were last updated on 4 June 2025 (among them most of
  the money, survey and balance of payments series) and 18 on 29 October 2025 (industrial
  production, producer prices and retail sales). Use these series only up to the vintage at
  which each one stopped.
* Nine series have no observations at all in any vintage from January 2015 to March 2024
  (five government bond yields, two raw material price series, two producer price series).
* Twenty-one survey series have no observations in the four vintages from February to
  June 2023.
* The 10 euro area unemployment series, the 14 employment series, the 6 exchange rate series and
  construction prices were behind for between 9 and 25 vintages in a row, up to the vintage of
  4 June 2025. They are up to date now, but those vintages do not show what was known at the
  time.
* In 296 cases in 2001–2014, covering 20 series, a rebuilt vintage holds values that the ECB's
  own file for that month leaves blank. Most are the five government bond yields from February
  2008 to March 2011, which the ECB had stopped publishing but did not remove from its log
  until 6 April 2011.

In all, 153 of the 278 series were behind or had no observations in at least two consecutive
vintages since 2015. Check these files before using a series:

* [output/series_status.csv](output/series_status.csv) says, in one line per series, whether
  it is current or has stopped in the latest vintage.
* [output/series_last_update.csv](output/series_last_update.csv) lists the last update of
  every series and how far behind it is.
* [output/series_gaps.csv](output/series_gaps.csv) lists, for each series, the vintages in
  which it is behind or has no observations.
* [output/series_status_by_vintage.csv](output/series_status_by_vintage.csv) gives the status
  of every series in every vintage (current, behind, no observations, not yet published).
* [output/old_file_check.csv](output/old_file_check.csv) lists the 296 cases. They are the rows
  whose status is "old file column empty, rebuilt vintage has data".

A series counts as behind in a vintage when either of two things is true. Its last observation
is older than was usual for that series in 2015–2022, by at least three months (monthly
series), two quarters (quarterly) or one year (annual). Or the ECB has left the series
unchanged for more vintages in a row than it ever did in those years. The thresholds are a
judgement. Halving or doubling them leaves the same 107 series behind in the latest vintage.

## Where the data are

`data/processed/vintages/` has one Excel workbook per variable per vintage, in the layout used
by ALFRED, the St. Louis Fed's archive of vintages. Folders are named by indicator and
variable, and the file name gives the vintage date:

```
data/processed/vintages/GDP/Real GDP/vintage_2008-06-04.xlsx
```

That file is euro area real GDP as the ECB had it on 4 June 2008. Its first sheet describes
the series (the ECB's title, series key, units, frequency and adjustment). Its second sheet
has two columns, the first day of each period and the value. All 278 variables are listed in
`data/processed/vintages/_variable_list.csv`.

A variable has a workbook only for the vintages in which it has observations. The first
vintage holds 183 of the 278 series. Another 45 start later in 2001, 26 in 2002 or 2003 and 24
in 2012.

`data/processed/raw_vintages/` has the same data as one CSV per series, with periods in rows
and vintage dates in columns, which is the easier form for studying revisions.

```python
import pandas as pd

# Real GDP as known on 4 June 2008 (the second sheet holds the data).
gdp = pd.read_excel("data/processed/vintages/GDP/Real GDP/vintage_2008-06-04.xlsx",
                    sheet_name=1, index_col=0)

# How real GDP for 2007 Q4 was revised over time.
raw = pd.read_csv("data/processed/raw_vintages/RTD.Q.S0.S.G_GDPM_TO_C.E.csv", index_col=0)
raw.loc["2007-Q4"]
```

## Taking only part of the data

Few people need all 278 series on all 261 dates. This opens a page in your browser for picking
the ones you do need:

```
python code/06_vintage_picker.py
```

Tick variables on the left (there is a search box; the folders tick as a whole) and vintage
dates on the right (all, the latest, the latest twelve, one a year, or a range), choose the
workbooks or the revision tables or both, and the page says how many files that is and roughly
how large before you ask for them. One click then downloads them as a single zip, with a
SELECTION.txt recording what was taken. Asking for the revision tables cuts them down to the
dates chosen, so one date gives a two-column file rather than a 261-column one.

Nothing about the stale series is left to be noticed. The page badges the variables that are
not kept up to date, strikes through the dates that hold nothing for the ones you have ticked
and marks the vintages in which one of them had fallen behind. The zip then records it in
writing: `not_up_to_date.csv` gives a row per run of dates in which a series you took was
behind or had no file, and SELECTION.txt counts them and says what the two words mean. A
selection cannot quietly come back short.

It needs no packages beyond the standard library and nothing from the internet. The server
listens on 127.0.0.1 only and reads only `data/processed/` and `output/`, so nothing leaves
the machine. `--port 8000` fixes the port and `--no-browser` leaves the browser alone.

## Layout

```
code/                         the scripts
code/06_vintage_picker.py     a page for downloading part of the data
code/vintage_picker.html      that page
data/raw/history/             the 278 logs as downloaded from the ECB
data/raw/series_metadata.csv  the list of series, with titles and units
data/raw/rtd_structure.xml    the ECB's code lists for the RTD
data/raw/old_files/           the ECB's vintage files for 2001-2014 (three zip files)
data/processed/raw_vintages/  one table per series, periods by vintage dates
data/processed/vintages/      one workbook per variable per vintage
data/processed/vintage_dates.csv     the 261 vintage dates
output/old_file_check.csv     the check against the 2001-2014 files, one row per series and month
output/series_status.csv      each series, current or stopped in the latest vintage
output/series_last_update.csv the last update of each series, and how far behind it is
output/series_gaps.csv        for each series, the vintages in which it is behind or empty
output/series_status_by_vintage.csv  the status of every series in every vintage
output/logs/                  what each script reported when it last ran
docs/                         the report: experience, method and checks
```

## Rebuilding the data

The scripts need Python 3 with pandas, xlsxwriter and openpyxl (`pip install -r requirements.txt`).
Run them from this folder. The first four build the data, in this order.

```
python code/01_download_rtd_history.py      # downloads the 278 logs from the ECB
python code/02_build_vintages.py            # rebuilds the vintage tables
python code/03_check_against_old_files.py   # compares them with the 2001-2014 files
python code/04_make_readable_vintages.py    # writes the workbooks
```

The first script is slow, because the ECB's server often takes one to three minutes to answer
a request for a series it has not been asked for recently. Running it again later picks up any
vintages the ECB has added since 3 October 2026. The other scripts take minutes.

Three more check the result. Each writes what it found to `output/logs/`.

```
python code/05_check_series_currency.py       # finds the series that are behind
python code/07_check_workbooks.py             # reopens 556 workbooks and compares them with the tables
python code/08_check_against_current_data.py  # compares the newest vintage with the ECB's data today
```

The last of these needs the internet. It guards against a log that the server cut short: on
5 October 2026 all 80,731 values of the newest vintage agreed with the data the ECB was
serving. Once the ECB publishes its next release, run the first script again before this one.

`06_vintage_picker.py` is the page described above and needs none of the others.

## Source

European Central Bank, euro area Real Time Database:
<https://data.ecb.europa.eu/data/datasets/RTD/data-information>.
The database is described in Giannone, Henry, Lalik and Modugno (2010), "An area-wide
real-time database for the euro area", ECB Working Paper 1145.
