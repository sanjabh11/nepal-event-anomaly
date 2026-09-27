# Global database of glacier break-off events (1900-2025)

## Description

This dataset provides a global compilation of glacier break-off events between 1900 and 2025, including ice avalanches, glacier detachments, and rock-ice avalanches associated with glacier flow.

The dataset was compiled from published literature, technical reports, hazard inventories, and verified observational records. Each event was manually reviewed and standardized.

---

## Dataset

The dataset is provided as an Excel file:

dataset/
 └─ glacier_breakoff_database.xlsx
    ├─  Europe
    ├─  Asia
    ├─  North America
    ├─  South America and New Zealand
    └─  References

Each row in the regional sheets represents a single glacier break-off event.

The References sheet contains all sources used for data compilation.

---

## Fields (Europe, Asia, North America, South America and New Zealand sheets)

Each record in the regional sheets contains the following fields:

- glacier_id - unique identifier of the source glacier; multiple events may correspond to the same glacier  
- event_name - name of the event  
- lon, lat - geographic coordinates (decimal degrees, WGS84)
- glacier_name - glacier name (if available)  
- country - country of occurrence  
- rgi_o1_region - first-order RGI v.7.0 region  
- rgi_o2_region - second-order RGI v.7.0 region  
- rgi_region_name - RGI v.7.0 region name  
- rgi_v7_id - RGI v7.0 glacier identifier (if available)   
- day, month, year - date components (if available)  
- date_min, date_max - time range for uncertain event timing (ISO 8601 format, MM-DD-YYYY where available)
- hazard_type - type of glacier break-off  
- total_volume - total mobilized volume ((m³, if reported)
- initial_volume - initial released volume (m³, if reported) 
- slope_detachment_zone - slope of the detachment zone (degrees, if reported)  
- triggers - reported triggers or conditioning factors  
- impact - reported impacts  
- references - source references  
- links - URLs to source materials  
- comments - additional notes  
- uncertainties - notes on data limitations  

---

## Fields (References sheet)

The References sheet contains:

- ID - reference identifier  
- region - geographic relevance (global, Europe, Asia, North America, South America and New Zealand)
- name - source title  
- link - DOI or URL  
- type - source type (e.g., scientific paper, report, inventory, news)  
- coverage - spatial coverage (global, regional, or local)
- area - geographic area  

---

## License

This dataset is distributed under the Creative Commons Attribution 4.0 International (CC BY 4.0) license.

---

## Citation

Bashkova, Ekaterina, and Rupper, Summer, 2026. Global database of glacier break-off events (1900-2025). Zenodo. https://doi.org/10.5281/zenodo.19477908
