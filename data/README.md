# Public food catalogue

Anses. 2025. Ciqual French food composition table.
Source: https://doi.org/10.5281/zenodo.17550133 (Anses, version 1, 19 November 2025).
Reuse under the Licence Ouverte with source and version attribution. See the dataset's
documentation and https://ciqual.anses.fr/ for methodology and reuse conditions.

`ciqual-2025.json` is a minimal projection of 3,484 official records: IDs, original
French and English names, EU-regulation energy and protein/carbohydrate/fat per 100 g.
Do not invent household portions, infer fluid density, or overwrite these source facts.
Unknown and censored/trace values remain null; nonnumeric source annotations are retained.
The mobile journal snapshots confirmed quantities rather than linking mutable totals.

Rebuild from these public files (kept out of Git because the projection is sufficient):

- `Table Ciqual 2025_ENG_2025_11_03.xlsx`, MD5 `a953e8c473d8cee24a32f89a48fbd54d`
- `alim_2025_11_03.xml`, MD5 `8e1171d63cee4b6010cfce25dd29243d`

Run `python scripts/build_ciqual_catalogue.py /path/to/table.xlsx /path/to/alim.xml`,
then `.venv/bin/alembic upgrade head` and `.venv/bin/python scripts/import_foods.py`.
The importer validates the pinned exports and updates by stable source ID in one transaction.
It never deletes private records. Refreshes require reviewing a new source release.

This first search increment covers Ciqual in both languages. USDA expansion and the separate
ODbL Open Food Facts product index are later increments; they are not silently mocked.
