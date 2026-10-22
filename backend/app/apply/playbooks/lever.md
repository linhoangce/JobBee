## Lever notes

- The posting at `jobs.lever.co/<company>/<id>` has **Apply for this job**, which leads to `/apply`, a single form.
- "Resume/CV" upload is first. Lever parses it and may prefill name, email, phone and current company. Verify these against the dossier.
- Links (LinkedIn, GitHub, Portfolio) are plain text fields. "Additional information" is an optional free-text box; leave it blank unless the dossier has a cover letter to paste.
- Some forms have an hCaptcha at the bottom. Do not try to solve it. Once all fields are filled, call `finish` with `ready_for_review`; if a captcha challenge appears on submit, the human handles it.
- The final button is **Submit application**.
