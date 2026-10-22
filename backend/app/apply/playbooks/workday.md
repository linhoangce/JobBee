## Workday notes

- Job page → **Apply** opens a "Start Your Application" modal. Choose **Autofill with Resume**: Workday reads the CV into My Information and My Experience, which saves typing in every job and school. Use **Apply Manually** only if Autofill is absent. Do not pick "Apply with LinkedIn" or "Use My Last Application", and do not press Escape or click the background Apply while the modal is open.
- After sign-in, Autofill asks for the resume: `file_upload` the tailored CV on the **Select file** button or drop zone (never click it), wait until it's parsed, then **Continue**.
- Workday needs an account for each company. On the Sign In page choose **Sign in with email** (never Apple or Google).
  - No saved account (the dossier says, and so does `site_account`): click **Create Account**, call `site_account` with action `create` (the email field, and both **Password** and **Verify New Password**), tick the terms checkbox if shown (a consent), then click **Create Account**.
  - If Workday asks you to verify your email, call `check_inbox` with purpose `verify_email`, then come back to the job and sign in with `site_account` action `sign_in`.
  - Saved account: `site_account` action `sign_in`, then **Sign In**.
  - "Account already exists", or sign-in says "wrong email address or password or your account might be locked": call `site_account` (`rejected`) and don't retry. Then **Forgot your password?** → `check_inbox` (`reset_password`) → `site_account` (`set_new_password`) → sign in. The applicant may have made this account themselves before.
- The create-account form has a hidden honeypot ("Enter website. This input is for robots only…"). Never fill fields like that.
- The application is a wizard: My Information → My Experience → Application Questions → Voluntary Disclosures → (Self Identify) → Review. Use **Save and Continue** / **Next** between pages.
- After Autofill, My Information and My Experience arrive filled from the CV. Read each page once and check every value against the dossier: name, address, phone, then each job's title, company and dates, each school, degree and field of study. Fix what the parser got wrong, fill what it left blank (degree and field of study often are), and delete entries the dossier doesn't have. If the CV isn't listed under Resume/CV, upload it there.
- With Apply Manually, My Experience starts empty: upload the CV with `file_upload` on **Select files** or the "Drop files here" zone (never click Select files), then add the dossier's jobs and schools. Don't add entries the dossier doesn't have.
- Dates are month/year boxes that `form_input` often doesn't take: click the month part, type the two-digit month, then the four-digit year (focus moves to the year by itself), and `zoom` to check.
- Dropdowns are custom widgets (`button` + listbox, often with a search box). Click to open, then pick the option. Prompt-style selectors ("How did you hear about us?") can be multi-level; drill in.
- Phone fields usually need "Phone Device Type" (Mobile) and a country code.
- Workday SPAs render slowly. If the page is blank or shows a spinner, `wait` a few seconds and re-read before deciding anything.
- The **Review** page lists everything with a final **Submit** button. Reaching it means you are done: call `finish` with `ready_for_review`.
