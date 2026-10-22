# Sites Genealogy Capsule works with

<!-- Generated from capsule/catalog/sites.toml by `uv run python -m capsule.catalog`. Edit that file, not this one. -->

Genealogy Capsule saves the page you are viewing, in your own browser session, when you
click Capture. It works on most record pages; the sites below are the ones it is known to
work with, and how well. Capsule is not affiliated with any of these sites. Pages from
subscription sites are kept only on your computer and never published.

Tried it on another site? Please say how it went in an issue, so the list can grow.

## Tested in real use

### [Ancestry](https://www.ancestry.com/)

- **Access:** Subscription
- **Pages:** Record pages, and record images through Ancestry's own Download button
- **Built-in handling:** record id read from the address, so a record is never captured twice; household members read from the record; accepts the image download from Ancestry's image server
- **Note:** For the image, arm the capture, then use the site's Download button.

### [RootsIreland](https://www.rootsireland.ie/)

- **Access:** Subscription
- **Pages:** Church and civil record transcription pages
- **Built-in handling:** different records shown at one search address are kept apart
- **Note:** Transcriptions only: the site shows no images.

### [NYC Municipal Archives, Historical Vital Records](https://a860-historicalvitalrecords.nyc.gov/)

- **Access:** Free
- **Pages:** Birth, marriage and death certificate pages, with the certificate PDF
- **Note:** Allow access to the certificate's file host when Capture asks, or only the page is saved.

### [IrishGenealogy.ie](https://www.irishgenealogy.ie/)

- **Access:** Free
- **Pages:** Civil and church record pages, and civil register images

### [FamilySearch](https://www.familysearch.org/)

- **Access:** Free (sign-up)
- **Pages:** Indexed record pages
- **Note:** The image viewer has not been tested yet.

### [Find a Grave](https://www.findagrave.com/)

- **Access:** Free
- **Pages:** Memorial pages and their photos
- **Note:** If you are signed in, a memorial is saved as private (gated); choose Public in the popup to change that.

### [Riksarkivet (Swedish National Archives)](https://sok.riksarkivet.se/)

- **Access:** Free
- **Pages:** Register images in the image viewer, through the site's own download
- **Built-in handling:** accepts the image download from the archive's image server; the image number in view is recorded
- **Note:** Images only: these pages have no text to read.

### [Archion](https://www.archion.de/)

- **Access:** Subscription
- **Pages:** Church register images in the viewer
- **Built-in handling:** the register page in view is recorded and cited; the archive and register named in the citation; warns when no page image was saved
- **Note:** Images only: these pages have no text to read.

### [National Library of Ireland, Catholic Parish Registers](https://registers.nli.ie/)

- **Access:** Free
- **Pages:** Register pages in the viewer
- **Built-in handling:** saves the whole page in view, not the viewer's tiles; the register and page cited; warns when no page image was saved
- **Note:** Images only: these pages have no text to read.

### [Chronicling America (Library of Congress)](https://www.loc.gov/collections/chronicling-america/)

- **Access:** Free
- **Pages:** Newspaper page views
- **Note:** Saves the page and a PDF of it; the full-resolution newspaper image is not saved yet.

### [Internet Archive](https://archive.org/)

- **Access:** Free
- **Pages:** Book pages in the reader
- **Note:** Allow access to the archive's image server when Capture asks, to save the scan. The reader's neighbouring pages are saved with it for now.

### [Trove (National Library of Australia)](https://trove.nla.gov.au/)

- **Access:** Free
- **Pages:** Newspaper articles, with their corrected text
- **Note:** May be saved as private (gated) even when you are signed out; choose Public in the popup to change that.

## Built-in support, not yet tested live

### [Matricula](https://data.matricula-online.eu/)

- **Access:** Free
- **Pages:** Church register images in the viewer
- **Built-in handling:** the register page in view is recorded and cited; warns when no page image was saved
- **Note:** Tested live: the page is saved, but the register image is not yet (the capture says so rather than reporting success).

### [Newspapers.com](https://www.newspapers.com/)

- **Access:** Subscription
- **Pages:** Newspaper page images, through the site's own Download (JPG)
- **Built-in handling:** accepts the image download from the site's image server
- **Note:** Page captures work; saving the page image with an armed download was fixed recently and awaits a live check.

### [National Archives of Ireland, Census 1821-1911](https://nationalarchives.ie/collections/search-the-census/)

- **Access:** Free
- **Pages:** Census household and person pages
- **Built-in handling:** each census record kept apart, whichever link you followed to it
- **Note:** Allow access to nationalarchives.ie when Capture asks, to save the census form images.

## Should work, not yet tested

### [Ellis Island passenger records](https://heritage.statueofliberty.org/)

- **Access:** Free (sign-up)
- **Pages:** Passenger records and manifests
- **Note:** Needs a free account; not tested yet.

### [American Ancestors (free databases)](https://www.americanancestors.org/)

- **Access:** Free (sign-up)
- **Pages:** Record pages in the free guest-member databases
- **Note:** Needs a free guest account; not tested yet.

### [NYS Historic Newspapers](https://nyshistoricnewspapers.org/)

- **Access:** Free
- **Pages:** Historic newspaper pages
- **Note:** The site may ask you to confirm you are human before showing a page.
