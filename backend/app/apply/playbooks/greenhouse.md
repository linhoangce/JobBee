## Greenhouse notes

- The application is a single page, either below the job description or behind an **Apply** button. Some companies embed it in an iframe on their own careers site.
- Resume and cover letter uploads offer "Attach", "Dropbox", "Google Drive" and "Enter manually". Use the file input (Attach) with the dossier's file.
- Location fields are often autocomplete inputs. Type the city, then pick the suggestion.
- Custom questions follow the standard fields. "Voluntary Self-Identification" (EEOC) questions can be declined.
- The final button is **Submit application** / **Submit**. When every required field (marked *) is filled, call `finish` with `ready_for_review`.
