# uk-property-data

Static data loaders for UK government datasets: IMD 2019 deprivation, Census 2021, MHCLG household projections, and UKCP18 climate projections.

## Loaders

- `IMDLookup` — English Indices of Deprivation 2019 (LSOA-level)
- `CensusLookup` — Census 2021 bulk table access
- `MHCLGLookup` — MHCLG household projections + Housing Delivery Test
- `UKCP18Lookup` — UK Climate Projections 2018 regional summaries

Each loader follows the same pattern:

```python
loader = IMDLookup.from_csv(path)   # user-supplied full dataset
loader = IMDLookup.from_default()   # bundled demo seed (10 rows)
row = loader.lookup("E01000001")
```
