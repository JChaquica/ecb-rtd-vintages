"""Write a series' vintages as workbooks, one per vintage, in the format ALFRED (the
St. Louis Fed's archival database) uses for a vintage.

Layout:  data/processed/vintages/<Indicator>/<Variable>/vintage_<DATE>.xlsx

    vintages/GDP/Real GDP/vintage_2008-06-04.xlsx
    vintages/Unemployment/Unemployment rate/vintage_2008-06-04.xlsx

Each workbook is one variable as it was known on one date. It has two sheets, the
same two as a vintage downloaded from ALFRED:

  README                 what the series is: title, source, units, frequency ...
  Vintage <DATE>         two columns: observation_date and the values, with the
                         column named <Variable>_<YYYYMMDD>

A variable has a file for a vintage date only if it had been published by then.
data/processed/variable_list.csv lists every variable with its folder, unit and
ECB key; make_variable_list() is how it is made.

Where the plain names come from: each RTD series has a concept code (the 4th part
of its key, e.g. G_GDPM_TO_C) with an official ECB name. The tables below translate
those codes into short names. The ECB's own title and description are written in
each README sheet and in variable_list.csv, so a short name can be checked.
"""

from __future__ import annotations

import datetime
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import xlsxwriter

from common import METADATA, STRUCTURE

# Code meanings from the ECB's RTD code lists
# (https://data-api.ecb.europa.eu/service/dataflow/ECB/RTD?references=all).
FREQUENCY = {"M": "Monthly", "Q": "Quarterly", "A": "Annual"}
AREA = {"S0": "Euro area", "US": "United States", "JP": "Japan"}
ADJUSTMENT = {"N": "not seasonally adjusted", "S": "seasonally adjusted",
              "W": "working day adjusted", "Y": "seasonally and working day adjusted"}
UNIT = {"EUR": "Euro", "USD": "US dollar", "JPY": "Japanese yen", "IX": "Index",
        "POINTS": "Points", "UNITS": "Units", "PS": "Persons", "PERS": "Persons",
        "PURE_NUMB": "Pure number", "PN": "Pure number", "PC": "Percent",
        "PT": "Percent", "PCPA": "Percent per annum", "PCCH": "Percentage change",
        "XDC_R_B1GQ": "Percent of GDP"}
MULTIPLIER = {"0": "", "3": "thousands", "6": "millions", "9": "billions"}
# Short form, used only to tell apart two versions of the same variable.
SHORT_ADJUSTMENT = {"N": "not adjusted", "S": "seasonally adjusted",
                    "W": "working day adjusted", "Y": "seasonally adjusted"}

# ---- Plain names -----------------------------------------------------------
# Economic sectors (used by value added, employment, unit labour costs).
SECTOR = {"TO": "total", "AHFF": "agriculture", "INDTO": "industry",
          "CONST": "construction", "WSHRTC": "trade, transport and hospitality",
          "INFCOM": "information and communication", "FININS": "finance and insurance",
          "REST": "real estate", "SCADM": "professional and business services",
          "DEFEDU": "public admin, education and health",
          "ARTOSER": "arts and other services", "MANUF": "manufacturing"}
# National accounts: C/O = volumes, U = current prices, D = deflator.
PRICE_BASIS = {"C": "real", "O": "real", "U": "nominal", "D": "deflator"}
EXPENDITURE = {"FCHI": "Private consumption", "FCGG": "Government consumption",
               "GFCF": "Investment", "XGS": "Exports of goods and services",
               "MGS": "Imports of goods and services", "DDIS": "Domestic demand",
               "CIAXDV": "Changes in inventories"}
INDUSTRY = {"INDTO": "incl. construction", "XCONS": "excl. construction",
            "XCOEN": "excl. construction and energy", "MANUF": "manufacturing",
            "CONST": "construction", "ENERG": "energy", "CAPGO": "capital goods",
            "INTGO": "intermediate goods", "CONGO": "consumer goods",
            "DCOGO": "durable consumer goods", "NCOGO": "non-durable consumer goods"}
HICP = {"OV": "all items", "XEFUN": "excluding energy and unprocessed food",
        "GOOD": "goods", "SERV": "services", "FOOD": "food, alcohol and tobacco",
        "FOODPR": "processed food", "FOODUN": "unprocessed food", "NRGY": "energy",
        "IGXE": "non-energy industrial goods", "HOUS": "housing services",
        "TRAN": "transport services", "COMM": "communication services",
        "RECR": "recreation and personal services", "MISC": "miscellaneous services"}
UNEMPLOYMENT = {"UNETO": "", "FETOT": ", women", "MATOT": ", men",
                "FMU25": ", under 25", "FMO25": ", 25 and over"}
TRADE_PRODUCT = {"TTT": "total", "CAP": "capital goods", "COM": "consumer goods",
                 "INT": "intermediate goods", "MAN": "manufactured goods", "OIL": "oil"}
TRADE_MEASURE = {"": "value", "U": "unit value index", "V": "volume index"}
STOCKS = {"DJE50": "Euro Stoxx 50", "DJESB": "Euro Stoxx (broad)",
          "DJEBM": "Euro Stoxx basic materials", "DJECG": "Euro Stoxx cyclical goods",
          "DJEEN": "Euro Stoxx energy", "DJEFI": "Euro Stoxx financials",
          "DJEHC": "Euro Stoxx healthcare", "DJEIG": "Euro Stoxx industrials",
          "DJENG": "Euro Stoxx non-cyclical goods", "DJETC": "Euro Stoxx telecommunications",
          "DJETE": "Euro Stoxx technology", "DJEUT": "Euro Stoxx utilities",
          "NIKKE": "Nikkei 225", "SP500": "S&P 500"}
RATES = {"EONIA": "Eonia", "EUR1M": "Euribor 1-month", "EUR3M": "Euribor 3-month",
         "EUR6M": "Euribor 6-month", "EUR1Y": "Euribor 1-year",
         "USL3M": "US dollar Libor 3-month", "JPL3M": "Japanese yen Libor 3-month",
         "U202Y": "Euro area government bond yield 2-year",
         "U203Y": "Euro area government bond yield 3-year",
         "U205Y": "Euro area government bond yield 5-year",
         "U207Y": "Euro area government bond yield 7-year",
         "U210Y": "Euro area government bond yield 10-year"}
EXCHANGE = {"EN00_BGR": "Euro nominal effective exchange rate, broad group",
            "EN00_NGR": "Euro nominal effective exchange rate, narrow group",
            "ERC0_BGR": "Euro real effective exchange rate (CPI deflated), broad group",
            "ERC0_NGR": "Euro real effective exchange rate (CPI deflated), narrow group",
            "ERP0_NGR": "Euro real effective exchange rate (PPI deflated), narrow group",
            "ERU1_NGR": "Euro real effective exchange rate (ULC deflated), narrow group"}
GOVERNMENT = {"UMD_CGG_D0": "General government balance",
              "UMS_CGG_D0": "General government primary balance",
              "UMD_CCG_D0": "Central government balance",
              "DEF_CSG_D0": "State government balance",
              "DEF_CLG_D0": "Local government balance",
              "DEF_CSF_D0": "Social security funds balance",
              "CEF_CGG_D0": "Government final consumption",
              "CEC_CGG_D0": "Government collective consumption",
              "CEI_CGG_D0": "Government individual consumption",
              "COE_CGG_D0": "Government compensation of employees",
              "INC_CGG_D0": "Government intermediate consumption",
              "CFC_CGG_D0": "Government consumption of fixed capital",
              "STM_CGG_DH": "Government social transfers in kind",
              "SAL_C0_DGG": "Government revenue from sales"}
# Balance of payments. The ECB's concept names for these codes are shifted by one
# item (e.g. the code named "current account, total" holds goods). The names below
# follow the ECB's series titles and source series, and the data confirm them:
# goods + services + primary income + secondary income = current account.
BALANCE_OF_PAYMENTS = {"XI1C": "Current account, credit", "XI1D": "Current account, debit",
                       "XA1N": "Current account balance",
                       "XA1C": "Goods, credit", "XA1D": "Goods, debit",
                       "XG1C": "Services, credit", "XG1D": "Services, debit",
                       "XS1C": "Primary income, credit", "XS1D": "Primary income, debit",
                       "XT1C": "Secondary income, credit", "XT1D": "Secondary income, debit",
                       "KA1C": "Capital account, credit", "KA1D": "Capital account, debit"}
RETAIL = {"RTXMOTVD": "Retail sales, total, real", "RTXMOTV": "Retail sales, total, nominal",
          "RTFOBETD": "Retail sales, food, beverages and tobacco, real",
          "RTNONFOD": "Retail sales, non-food, real",
          "RTTEXCLD": "Retail sales, textiles and clothing, real",
          "RTHOUSED": "Retail sales, household equipment, real"}
LABOUR_COST = {"H_WXAFG": "Labour cost index, total",
               "HW_WXAFG": "Labour cost index, wages and salaries",
               "HC_WXAFG": "Labour cost index, non-wage costs",
               "H_XCONS": "Labour cost index, industry excluding construction",
               "H_CONST": "Labour cost index, construction",
               "H_SERV": "Labour cost index, services"}
MONEY = {"M1_V_NC": "M1", "M2_V_NC": "M2", "M3_V_NC": "M3", "M2": "M2",
         "M3BM_V_NC": "M3 annual growth rate", "M3BM_C_WN": "Base money"}
LOANS = {"LOAN_U_NG": "Loans to the private sector",
         "LOANSEC_U_NG": "Credit to the private sector"}

NOTES = {
    "G_TLSP_TO_U": "Check before use: in later vintages this series holds nominal GDP, "
                   "not taxes less subsidies (the ECB links it to a GDP source series).",
    "C_DJECG": "ECB concept name says cyclical goods; ECB series title says Consumer staples.",
    "C_DJENG": "ECB concept name says non-cyclical goods; ECB series title says Consumer discretionary.",
    "P_C_OV": "For the United States and Japan this is the national CPI, not HICP.",
}

# The ECB codes the adjustment of these euro area series the wrong way round: the series
# it codes N (not adjusted) holds the seasonally adjusted figures, and the one it codes Y
# the unadjusted ones. Comparison with the ECB's BSI dataset shows it, and so do the
# series themselves: from 2010 to mid-2025 the month-to-month changes of each N series
# are 18 to 29 per cent smaller than those of its Y twin.
# They are named and labelled here by what the figures are, and their note gives the
# ECB's code. M3 annual growth (M_M3BM_V_NC) is coded correctly.
ADJUSTMENT_REVERSED = {"M_M1_V_NC", "M_M2_V_NC", "M_M3_V_NC", "M_LOAN_U_NG", "M_LOANSEC_U_NG"}


def plain_name(concept: str, denom: str) -> tuple[str, str]:
    """(folder, variable name) for one ECB concept code, e.g. G_GDPM_TO_C."""
    group, rest = concept[0], concept[2:]
    parts = rest.split("_")
    if group == "G":
        item, sector, basis = parts[0], parts[1], PRICE_BASIS[parts[2]]
        if item == "GDPM":
            return "GDP", {"real": "Real GDP", "nominal": "Nominal GDP",
                           "deflator": "GDP deflator"}[basis]
        if item == "GVAD":
            return "Gross value added", f"Gross value added, {SECTOR[sector]}, {basis}"
        if item == "TLSP":
            return "Gross value added", f"Taxes less subsidies on products, {basis}"
        return "GDP expenditure components", f"{EXPENDITURE[item]}, {basis}"
    if group == "L":
        if parts[0] in UNEMPLOYMENT:
            what = "Unemployment rate" if denom == "F" else "Unemployed persons"
            return "Unemployment", what + UNEMPLOYMENT[parts[0]]
        if parts[0] == "TEMP":
            return "Employment", f"Employment, {SECTOR[parts[1]]}"
        if parts[0] in ("EMP", "SEMP"):
            return "Employment", {"EMP": "Employees", "SEMP": "Self-employed"}[parts[0]]
        if parts[0] == "ULC":
            return "Unit labour costs", f"Unit labour costs, {SECTOR[parts[1]]}"
        return "Labour costs", LABOUR_COST[rest]
    if group == "P":
        if parts[0] == "C":
            if denom == "A":
                return "Consumer prices (HICP)", f"HICP inflation rate, {HICP[parts[1]]}"
            return "Consumer prices (HICP)", f"HICP, {HICP[parts[1]]}"
        if parts[0] == "P":
            return "Producer prices", f"Producer prices, {INDUSTRY[parts[1]]}"
        if parts[0] == "CO":
            return "Construction prices", "Construction prices, residential buildings"
        return "Commodity prices", {"OILBR": "Oil price, Brent crude",
                                    "R_TO": "Raw material prices, total",
                                    "R_XNRGY": "Raw material prices, excluding energy"}[rest]
    if group == "I":
        if rest == "NCARS":
            return "New car registrations", "New car registrations"
        return "Industrial production", f"Industrial production, {INDUSTRY[rest]}"
    if group == "O":
        return "Retail sales", RETAIL[rest]
    if group == "M":
        if rest in LOANS:
            return "Loans and credit", LOANS[rest]
        return "Money supply", MONEY[rest]
    if group == "C":
        if rest in STOCKS:
            return "Stock prices", STOCKS[rest]
        return "Interest rates and bond yields", RATES[rest]
    if group == "E":
        return "Exchange rates", EXCHANGE[rest]
    if group == "F":
        return "Government finance", GOVERNMENT[rest]
    if group == "T":
        direction = {"M": "imports", "X": "exports"}[parts[0][0]]
        measure = TRADE_MEASURE[parts[1] if len(parts) > 1 else ""]
        return (f"Goods {direction}",
                f"Goods {direction}, {TRADE_PRODUCT[parts[0][1:]]}, {measure}")
    if group == "X":
        return "Balance of payments", BALANCE_OF_PAYMENTS[parts[0]]
    raise KeyError(concept)


def survey_names() -> dict[str, str]:
    """Survey concept codes (Y_...) to the ECB's own concept names."""
    # The survey names are already plain English, so they are read from the ECB
    # code list in data/raw/rtd_structure.xml rather than retyped here.
    names = {}
    root = ET.parse(STRUCTURE).getroot()
    ns = {"s": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
          "c": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common"}
    for codelist in root.iter("{%s}Codelist" % ns["s"]):
        if codelist.get("id") == "CL_RT_ECON_CONCEPT":
            for code in codelist.findall("s:Code", ns):
                names[code.get("id", "")] = code.findtext("c:Name", "", ns)
    return names


def make_variable_list() -> pd.DataFrame:
    """One row per series: its folder, its plain name and its description."""
    meta = pd.read_csv(METADATA, dtype=str).fillna("")
    ecb_names = survey_names()
    rows = []
    for m in meta.to_dict("records"):
        area = m["KEY"].split(".")[2]
        if m["RT_ECON_CONCEPT"].startswith("Y_"):
            folder = "Surveys"
            name = ecb_names[m["RT_ECON_CONCEPT"]].replace(" - ", ", ")
        else:
            folder, name = plain_name(m["RT_ECON_CONCEPT"], m["RT_DENOM"])
        if area != "S0":
            name = name.replace("HICP", "Consumer prices (CPI)") + f" - {AREA[area]}"
        multiplier = MULTIPLIER[m["UNIT_MULT"]]
        note = NOTES.get(m["RT_ECON_CONCEPT"], "")
        if m["RT_ECON_CONCEPT"] == "P_C_OV" and area == "S0":
            note = ""                            # the CPI note is about the US and Japan only
        adjustment = m["ADJUSTMENT"]
        if m["RT_ECON_CONCEPT"] in ADJUSTMENT_REVERSED and area == "S0":
            adjustment = {"N": "S", "Y": "N"}[m["ADJUSTMENT"]]
            other = "Y" if m["ADJUSTMENT"] == "N" else "N"
            note = (f"The ECB codes this series {m['ADJUSTMENT']} "
                    f"({ADJUSTMENT[m['ADJUSTMENT']]}), but its figures are "
                    f"{ADJUSTMENT[adjustment]}; the series it codes {other} holds the "
                    f"{'unadjusted' if other == 'Y' else 'adjusted'} figures.")
        rows.append({
            "Folder": folder, "Frequency": FREQUENCY[m["FREQ"]], "Variable": name,
            "Unit": UNIT[m["UNIT"]] + (f", {multiplier}" if multiplier else ""),
            "Adjustment": ADJUSTMENT[adjustment], "Area": AREA[area],
            "Notes": note,
            "ECB title": m["TITLE"], "ECB description": m["TITLE_COMPL"],
            "ECB concept name": ecb_names[m["RT_ECON_CONCEPT"]],
            "ECB series key": m["KEY"], "ECB source series": m["DOM_SER_IDS"],
            "short adjustment": SHORT_ADJUSTMENT[adjustment],
        })
    v = pd.DataFrame(rows)

    # Same name twice in a folder (e.g. adjusted and unadjusted versions): add
    # what differs. First the adjustment, then the unit if still not unique.
    for extra in ("short adjustment", "Unit"):
        twice = v.duplicated(["Folder", "Variable"], keep=False)
        v.loc[twice, "Variable"] += " (" + v.loc[twice, extra].str.lower() + ")"
    bad = v[v["Variable"].str.contains(r'[\/:*?"<>|]')]
    if len(bad):
        raise ValueError(f"names with characters Windows does not allow: {list(bad['Variable'])}")
    if v.duplicated(["Folder", "Variable"]).any():
        raise ValueError("two series still share a name:\n"
                         f"{v[v.duplicated(['Folder', 'Variable'], keep=False)]}")
    return v.drop(columns="short adjustment").sort_values(["Folder", "Variable"])


SOURCE = "Euro Area Real-Time Database (RTD), European Central Bank"
LINK = "https://data.ecb.europa.eu/data/datasets/RTD/data-information"
MAX_PATH = 218      # Excel cannot open a file whose full path is longer than this


def observation_date(period: str) -> datetime.date:
    """First day of a period: 2005-Q3 -> 2005-07-01, 2005-07 -> 2005-07-01, 2005 -> 2005-01-01."""
    year = int(period[:4])
    if "Q" in period:
        month = 3 * int(period[-1]) - 2
    elif "-" in period:
        month = int(period[5:7])
    else:
        month = 1
    return datetime.date(year, month, 1)


def write_vintage(path: Path, s: dict, date: str, last_date: str, values: pd.Series,
                  created: str) -> None:
    """One workbook in ALFRED's layout: a README sheet and a two-column data sheet."""
    with xlsxwriter.Workbook(path) as book:
        readme = book.add_worksheet("README")
        readme.set_column(0, 0, 86)
        readme.set_column(1, 2, 15)
        rows = [[f"Series ID: {s['ECB series key']}"], [SOURCE], [f"Link: {LINK}"],
                ["Output Format: Observations by Vintage Date, All Observations"],
                [f"File Created: {created}"], [],
                ["Title", "Real-Time Start", "Real-Time End"]]
        span = [s["First vintage"], last_date]
        rows.append([s["Variable"], *span])
        fields = [("ECB Title", s["ECB title"]), ("ECB Description", s["ECB description"]),
                  ("Area", s["Area"]), ("Units", s["Unit"]), ("Frequency", s["Frequency"]),
                  ("Seasonal Adjustment", s["Adjustment"].capitalize()), ("Notes", s["Notes"])]
        for label, text in fields:
            if text:
                rows += [[], [label], [text, *span]]
        rows += [[], ["Vintage Date Specified:"], [date]]
        for r, row in enumerate(rows):
            readme.write_row(r, 0, row)

        data = book.add_worksheet(f"Vintage {date}")
        data.set_column(0, 0, 17)
        data.set_column(1, 1, 30)
        as_date = book.add_format({"num_format": "yyyy-mm-dd"})
        data.write_row(0, 0, ["observation_date", f"{s['Variable']}_{date.replace('-', '')}"])
        for r, (period, value) in enumerate(values.items(), start=1):
            data.write_datetime(r, 0, observation_date(str(period)), as_date)
            if value == value:                   # a gap inside the series stays blank
                data.write_number(r, 1, value)


def write_series(folder: Path, s: dict, table: pd.DataFrame, last_date: str,
                 created: str) -> int:
    """Write one workbook per vintage (column) of a table that has any value in it.

    The workbooks go to a folder named <folder>.part first, which then takes the
    place of <folder>, so an interrupted run never leaves a variable half written.
    Returns the number of workbooks.
    """
    part = folder.with_name(folder.name + ".part")
    if part.exists():
        shutil.rmtree(part)
    part.mkdir(parents=True)
    n = 0
    for date in table.columns:
        values = table[date]
        if values.notna().any():
            # From the first to the last period that has a value on this date.
            values = values.loc[values.first_valid_index():values.last_valid_index()]
            write_vintage(part / f"vintage_{date}.xlsx", s, date, last_date, values, created)
            n += 1
    if folder.exists():
        shutil.rmtree(folder)
    part.rename(folder)
    return n
