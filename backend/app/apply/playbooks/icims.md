## iCIMS notes

- iCIMS portals (`careers-<company>.icims.com`) render the application inside an iframe. Work inside it. If refs go stale after each page, re-read the page.
- An email-first login/registration step is common. Enter the applicant's email, then create the account or sign in with `site_account`; verify the email with `check_inbox` if asked.
- The flow is a multi-page wizard (profile, resume, questions, EEO, e-signature). Advance with **Next** / **Save & Continue**.
- E-signature or "I certify" pages are attestations: follow the consent rules (sign with the full name and the dossier's date, and list it as a consent).
- The final step's button is **Submit** / **Finish**. Call `finish` with `ready_for_review` there.
