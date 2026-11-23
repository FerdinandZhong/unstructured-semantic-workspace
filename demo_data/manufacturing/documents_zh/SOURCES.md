# Chinese contract samples: sources

Prepared on 23 November 2026. These are demo documents, not real contracts. Not ingested and not listed in any manifest.

Three files fill official Chinese model contract texts with sample values. Three files are synthetic documents written for this demo.
Parties, products, orders and vendors come from the AdventureWorks sample (Microsoft, MIT licence, fictional companies; see `../SOURCES.md`).
"示例自行车制造有限公司" and "示例市" are synthetic names. Addresses, phone numbers, ID numbers, bank accounts and credit codes are placeholders (合成占位).
Amounts are in USD, as in the sample data. Each contract says so in its "other terms" clause.

## Official model texts

The texts come from the model contract library of the State Administration for Market Regulation (国家市场监督管理总局合同示范文本库), retrieved 23 November 2026.
The library's reuse statement: "合同示范文本库收集了市场监管部门制定发布的合同示范文本，供社会公众查阅下载。当事人可以参照各类合同示范文本订立合同。" The page footer says "版权所有：国家市场监督管理总局".
No explicit licence is stated. Each derived file therefore keeps the number and issuing body visible and says it is a filled sample.

| Number | Title | Issuing body | Source page | File used (SHA-256 of download) |
|---|---|---|---|---|
| BTHF-2023-0102 | 京津冀地区工业品买卖合同 | 北京市市场监督管理局、天津市市场监督管理委员会、河北省市场监督管理局 | https://htsfwb.samr.gov.cn/View?id=8c9b5b96-d6f9-437a-b82d-7e1872ae50ab | DOCX https://htsfwb.samr.gov.cn/api/File/DownTemplate?id=8c9b5b96-d6f9-437a-b82d-7e1872ae50ab&type=1 (`635bf49d6134ee2ce1bac7aacacb814199464f1a10fdb3938984270f237199a6`) |
| GF-2000-0101 | 工业品买卖合同（示范文本） | 国家工商行政管理总局 | https://htsfwb.samr.gov.cn/View?id=f10475f4-cc79-445a-9eb6-7243f416322d | DOCX https://htsfwb.samr.gov.cn/api/File/DownTemplate?id=f10475f4-cc79-445a-9eb6-7243f416322d&type=1 (`bde75861c5de985bc34571c16999c6c58465f8e37f859e7447a04361cb547e90`) |
| GF-2000-0301 | 加工合同（示范文本） | 国家工商行政管理总局 | https://htsfwb.samr.gov.cn/View?id=95c6cba3-0f9a-4475-abe7-b592198f2d87 | DOCX https://htsfwb.samr.gov.cn/api/File/DownTemplate?id=95c6cba3-0f9a-4475-abe7-b592198f2d87&type=1 (`c56e8d6bf23420699262fc13a21f959ab3b65c1c765a1efc5dc2ace784265cb1`) |

`official_text/*.md` holds the clause text used, extracted from the DOCX with wording unchanged. Blanks are marked `{{name}}`, boxes `{{cb:name}}`.
Omitted: the BTHF guidance section 说明与提示, and page numbers and the printer line in the GF forms.
`python build_zh_documents.py --check --source-dir <folder with the DOCX files>` verifies that the kept wording matches the source outside the blanks.

## Produced files

| File | Title | Kind | Based on | Pages | SHA-256 |
|---|---|---|---|---|---|
| MFG-ZH-01.pdf | 京津冀地区工业品买卖合同（Efficient Cycling，SO57159） | official model text, filled | BTHF-2023-0102 | 3 | `fec1a5ee7fd3c9f744cec56b66052c22dd7cd3a4895442ec2316e93950a62600` |
| MFG-ZH-02.pdf | 工业品买卖合同（Tandem Bicycle Store，SO50234） | official model text, filled | GF-2000-0101 | 2 | `ac7d208fbedcc68ab7834b7470ad185dad0d32b0badb683dfa746cb7d2f101a3` |
| MFG-ZH-03.pdf | 加工合同（The Bicycle Accessories Company，Road-650 Red, 44） | official model text, filled | GF-2000-0301 | 2 | `3e280149e17edd1a2dea4abda71b3eb7e81828277e8d6c8bdcbedf0921b271c8` |
| MFG-ZH-04.pdf | 产品采购框架协议（First Department Stores） | synthetic | none | 1 | `edc10d5bd3d77899e5049113d44b1430f57900baa84711d678b513dd37a2de1f` |
| MFG-ZH-05.pdf | 工业品买卖合同补充协议（第1号），amends MFG-ZH-01 | synthetic | none | 1 | `b0685b6d080a0a256ace6920b7ade32dec582e58b4435c509f237c5c609d0eb4` |
| MFG-ZH-06.pdf | 质量保证及验收协议（Vista Road Bikes） | synthetic | none | 1 | `09e94a2bf667e0bc3a4c9fa76525f1600b0ef95e2296393a3bc3b344c006c453` |

Every page footer and a first-page banner state, in Chinese and English, that the file is sample data for demonstration. They also state either the model text number and issuing body, or "synthetic document".

## Rebuild

`python build_zh_documents.py` (PyMuPDF; reads `../data/csv/` and `official_text/`). The output is byte-identical between runs on this machine.
The font is Arial Unicode MS from macOS, embedded as a subset. Another font version changes the hashes above.
`links.json` is the answer key, and `questions.md` lists demo questions.
