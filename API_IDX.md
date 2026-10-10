# Sectors API v2 — Referensi Endpoint Indonesia (IDX)

Seluruh 35 endpoint IDX, ditarik dari indeks resmi `https://docs.sectors.app/llms.txt`.
Base URL: `https://api.sectors.app` — autentikasi lewat header `Authorization: <API_KEY>` (mentah, tanpa `Bearer`).

Kolom **Dipakai** menandai endpoint yang sudah terhubung di `app/sectors.py`.

## Company Screener

| Path | Endpoint | Kredit | Dipakai |
|---|---|---|---|
| `/v2/companies/` | [Companies Screener](https://docs.sectors.app/api-references/v2/indonesia/screener/companies) | 1 | ya |
| `/v2/free-float/` | [Free Float Market Analysis](https://docs.sectors.app/api-references/v2/indonesia/screener/free-float) | 1 | — |

## Helper Lists

| Path | Endpoint | Kredit | Dipakai |
|---|---|---|---|
| `/v2/companies/list_companies_with_segments/` | [Companies with Revenue Segments](https://docs.sectors.app/api-references/v2/indonesia/helper-list/companies-segments-list) | 1 | — |
| `/v2/companies/quarterly-financial-dates/` | [Latest Quarterly Financial Dates (Universe)](https://docs.sectors.app/api-references/v2/indonesia/helper-list/latest-quarterly-dates) | 1 | — |
| `/v2/company/get_quarterly_financial_dates/{symbol}/` | [Quarterly Financial Dates](https://docs.sectors.app/api-references/v2/indonesia/helper-list/company-quarterly-dates) | 1 | — |
| `/v2/industries/` | [Industries](https://docs.sectors.app/api-references/v2/indonesia/helper-list/industries) | 1 | — |
| `/v2/subindustries/` | [Subindustries](https://docs.sectors.app/api-references/v2/indonesia/helper-list/subindustries) | 1 | — |
| `/v2/subsectors/` | [Subsectors](https://docs.sectors.app/api-references/v2/indonesia/helper-list/subsectors) | 1 | — |
| `/v2/tags/` | [News Tags](https://docs.sectors.app/api-references/v2/indonesia/helper-list/tags) | 1 | — |

## Company

| Path | Endpoint | Kredit | Dipakai |
|---|---|---|---|
| `/v2/company/corporate-actions/{symbol}/` | [Corporate Actions](https://docs.sectors.app/api-references/v2/indonesia/company/corporate-actions) | 1 | — |
| `/v2/company/shareholders-composition/{symbol}/` | [Shareholders Composition](https://docs.sectors.app/api-references/v2/indonesia/company/shareholders-composition) | 1 | — |

## Detailed Reports

| Path | Endpoint | Kredit | Dipakai |
|---|---|---|---|
| `/v2/company/get-segments/{symbol}/` | [Company Revenue Segments](https://docs.sectors.app/api-references/v2/indonesia/report/company-segments) | 1 | — |
| `/v2/company/report/{symbol}/` | [Company Report](https://docs.sectors.app/api-references/v2/indonesia/report/company-report) | 1 | ya |
| `/v2/financials/quarterly/{symbol}/` | [Company Quarterly Financials](https://docs.sectors.app/api-references/v2/indonesia/report/quarterly-financials) | 1 | — |
| `/v2/subsector/report/{sub_sector}/` | [Subsector Report](https://docs.sectors.app/api-references/v2/indonesia/report/sector-report) | 1 | ya |

## Transaction Data

| Path | Endpoint | Kredit | Dipakai |
|---|---|---|---|
| `/v2/close/` | [Daily Full-Universe Close](https://docs.sectors.app/api-references/v2/indonesia/transaction/close) | 1 | — |
| `/v2/daily/{symbol}/` | [Daily Transaction Data](https://docs.sectors.app/api-references/v2/indonesia/transaction/daily) | 1 | ya |
| `/v2/idx-total/` | [IDX Market Summary](https://docs.sectors.app/api-references/v2/indonesia/transaction/idx-total) | 1 | ya |
| `/v2/index-daily/` | [Daily Full-Universe Index Close](https://docs.sectors.app/api-references/v2/indonesia/transaction/index-daily-universe) | 1 | — |
| `/v2/index-daily/{index_code}/` | [Index Daily Transaction Data](https://docs.sectors.app/api-references/v2/indonesia/transaction/index-daily) | 1 | ya |

## Rankings

| Path | Endpoint | Kredit | Dipakai |
|---|---|---|---|
| `/v2/companies/top-changes/` | [Top Company Movers](https://docs.sectors.app/api-references/v2/indonesia/ranking/top-changes) | 1 per kombinasi | — |
| `/v2/most-traded/` | [Most Traded Stocks](https://docs.sectors.app/api-references/v2/indonesia/ranking/most-traded) | 2 | — |

## IPO & Performance

| Path | Endpoint | Kredit | Dipakai |
|---|---|---|---|
| `/v2/listing-performance/{symbol}/` | [Company IPO & Listing Performance](https://docs.sectors.app/api-references/v2/indonesia/ipo/listing-performance) | 1 | — |

## News & Filings

| Path | Endpoint | Kredit | Dipakai |
|---|---|---|---|
| `/v2/corporate-actions/` | [Corporate Actions Calendar](https://docs.sectors.app/api-references/v2/indonesia/news/corporate-actions) | 1 | — |
| `/v2/filings/` | [Company Filings](https://docs.sectors.app/api-references/v2/indonesia/news/filings) | 1 | ya |
| `/v2/news/` | [News Articles](https://docs.sectors.app/api-references/v2/indonesia/news/news) | 1 | ya |
| `/v2/suspensions/` | [Stock Suspensions](https://docs.sectors.app/api-references/v2/indonesia/news/suspensions) | 1 | — |

## Brokers

| Path | Endpoint | Kredit | Dipakai |
|---|---|---|---|
| `/v2/broker-activity/{broker_code}/` | [Broker Activity By Code](https://docs.sectors.app/api-references/v2/indonesia/brokers/broker-activity-by-code) | 1 | — |
| `/v2/broker-activity/{broker_code}/top/` | [Top Accumulations and Distributions Per Broker](https://docs.sectors.app/api-references/v2/indonesia/brokers/broker-activity-top) | 2 | — |
| `/v2/broker-summary/{symbol}/` | [Broker Activity Per Symbol](https://docs.sectors.app/api-references/v2/indonesia/brokers/broker-summary-by-symbol) | 1 | — |
| `/v2/broker-summary/{symbol}/top/` | [Top Buyers and Sellers Per Symbol](https://docs.sectors.app/api-references/v2/indonesia/brokers/broker-summary-top) | 2 | — |
| `/v2/brokers/` | [Broker Registry](https://docs.sectors.app/api-references/v2/indonesia/brokers/broker-registry) | 1 | — |
| `/v2/brokers/top/` | [Top Brokers Daily Ranking](https://docs.sectors.app/api-references/v2/indonesia/brokers/top) | 2 | — |
| `/v2/foreign-flow/` | [Daily Full-Universe Foreign Flow](https://docs.sectors.app/api-references/v2/indonesia/brokers/foreign-flow) | 1 | — |
| `/v2/foreign-flow/{symbol}/` | [Daily Net Foreign Inflow](https://docs.sectors.app/api-references/v2/indonesia/brokers/foreign-flow-by-symbol) | 1 | ya |

## Catatan kredit

- Status 2xx menagih kredit sesuai biaya endpoint; 404 tetap menagih 1 kredit.
- Status 400, 401, 403, 429, dan 5xx gratis.
- Screener: query terstruktur (`where`) 1 kredit, query bahasa alami (`q`) 3 kredit.
- Laporan multi-bagian dan ranking multi-klasifikasi menagih lebih dari 1.
- Top Company Movers menagih 1 kredit per kombinasi klasifikasi x periode. Tanpa
  parameter, default 2 klasifikasi x 5 periode = 10 kredit sekali panggil. Batasi
  dengan `classifications` dan `periods` kalau tidak butuh semuanya.

## Status verifikasi

Path diekstrak dari spesifikasi OpenAPI yang tertanam di tiap halaman dokumentasi,
bukan ditebak. Sembilan endpoint yang sudah dipakai aplikasi terverifikasi jalan, plus
`/v2/subsectors/` dan `/v2/companies/top-changes/` yang diuji langsung ke API
(keduanya 200 OK). Sisanya belum diprobe untuk menghemat kredit.
