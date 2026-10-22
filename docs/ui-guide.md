# Try JobBee in the browser

Everything works from the web app. Telegram is optional and only mirrors the same cards.

## Start

Start the backend **without `--reload`**. A reload restarts the server, which drops any application in progress. From `backend\`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then start the frontend from `frontend\` with `npm.cmd run dev`, and open http://localhost:5173.

You need credit on your Anthropic account (console.anthropic.com → Billing). Without it, every Claude step fails with "credit balance is too low", and the app shows that error on the card.

## 1. Welcome (first run only)

1. **Connect Claude.** Paste an API key. JobBee checks it with Anthropic and saves it in Windows Credential Manager.
   - If `ANTHROPIC_API_KEY` is already in `backend/.env`, this step is skipped.
2. **Your resume.** Drop a PDF or Word file. Claude Haiku reads it, and you confirm the summary.
   - If you already have a resume and profile, click **Continue with this**.
3. **What you want.** Fill in:
   - roles and places;
   - remote, hybrid or on-site;
   - minimum salary;
   - work authorization and sponsorship;
   - how many jobs a day.

   **Start finding jobs** runs the first search and takes you to Home.

Already onboarded? Open http://localhost:5173/welcome to see the flow again.

## 2. Home

- **Agent bar.** Shows what JobBee is looking for, when it searches next, how many cards it sent today, and today's Claude spend.
  - **Find jobs now** runs a search right away.
  - **Paste a job link** works for Greenhouse, Lever, Ashby, Workday and most company career pages.
  - **Pause** stops new searches and applications.
- **Needs you** is the only place you have to act. Each card type works like this:

| Card | What to do |
| --- | --- |
| **New match** | Read the fit score, why it fits, the gaps, and what changed in your CV. **View CV** opens the tailored PDF; the **Style** menu re-renders this CV and cover letter in another style in a few seconds (no Claude call). If you replaced your resume after the card was made, it says so: **Update CV** tailors it again from the new one (about a minute). Then click **Apply**, or **Skip** with a reason ("Not this company" excludes the company from now on). |
| **Ready to submit** | Check the screenshot and the answers. **JobBee's calls and consents** come first: answers it decided itself (where your profile had none) and terms or attestations it accepted for you. **Change** any answer and JobBee edits the open form (about a minute). Then click **Submit application**, or **Don't submit**. Its calls and your changes are saved on Submit, so it answers the same way next time. The form stays open for 45 minutes; after that, **Fill it again** needs nothing from you. |
| **For when you're at your PC** | Something only a person can do (a CAPTCHA, a phone code, a required ID or bank field). The other applications carry on. When you're at your PC, click **I'm at my PC, try now**: JobBee starts again and hands you the browser for that step. Or **Skip this job**. |
| **Needs you at your PC** | Only after **I'm at my PC**: do the step in the JobBee Chrome window, then click **Done, continue**. |
| **Did it go through?** | Shown when JobBee clicked Submit but saw no confirmation. Check the tab, then answer. |

- **Applying now** shows the application being filled, with its latest step, plus any queued ones. JobBee fills one form at a time.
  - Chrome opens for it, and each tab closes when its application is done. Chrome quits once no tab is left.
  - A tab stays open while its form waits for your Submit, when JobBee couldn't confirm a submit, and after you stop a run.
- **Your applications** lists every job by outcome. Click a row to see:
  - the fit, the tailored CV, the posting, and every step the agent took, with its cost;
  - **Prepare anyway** for a job screened out as a poor fit;
  - **Fill the form again** after a failure or timeout.
  - what the agent did: accounts it made or signed in to, emails it used, consents it gave, its judgement calls, every answer, and every step.

## 3. Settings

| Section | What's there |
| --- | --- |
| **Job preferences** | Everything from welcome step 3, plus the lowest match score, the daily search time, how old a posting can be, and companies to never send. |
| **Where to look** | Web search on or off; a watchlist of companies (by name or careers-page link) whose Greenhouse, Lever or Ashby board is checked on every search; LinkedIn alert emails through Google sign-in. |
| **Applying for you** | Whether JobBee may create and sign in to job-site accounts (with your email and a password it saves in Windows Credential Manager), and accept terms and sign attestations with your name. Both are on by default. Below: the accounts it made, with **Show** for the password and a bin to forget one. Connect Gmail (Where to look) so it can confirm new accounts from your inbox. |
| **Resume & answers** | Replace your resume, set common form answers (phone, address, relocation), and see the answers you gave the agent. |
| **CV style** | Pick how new CVs and cover letters look: Modern, Classic, Minimal, Compact or Bold. Each tile previews your latest tailored CV (a sample until you have one); switch between CV and cover letter previews, or open one at full size. All styles are single-column text that application systems can read. Every tailored CV keeps to your own resume's page count: Claude is asked to keep the same length, and if it still runs over, the PDF tightens spacing, margins and then type size (never below 88%) until it fits. |
| **Claude** | Key status, today's and this month's spend, daily and monthly limits, and which model does what. |
| **Phone updates** | Optional Telegram (see `chat-agent.md`). |

## A good first test

1. Paste the link to a real, low-stakes Greenhouse or Lever posting into the agent bar.
2. Within about a minute a **New match** card appears. Open **View CV** to check the PDF.
3. Click **Apply**. Chrome opens and Claude fills the form by itself, including any sign-up. You don't need to do anything.
4. When **Ready to submit** appears, check every answer, then click **Submit application**. You should see "Applied" in your applications.

## Limits right now

- The PC must be on with JobBee running. Searches missed while it was off run when it's back on.
- JobBee fills one application at a time. Up to 2 filled forms can wait for your Submit.
- JobBee never solves CAPTCHAs or phone codes, never fills ID numbers or bank details, and never uses "Sign in with Google/Apple/LinkedIn". Those jobs are set aside for you.
- A site that emails a verification link needs Gmail connected (read-only). Without it, that job is set aside.
- If you already have an account on a site (you made it yourself), JobBee doesn't know its password. The site refuses JobBee's sign-up or sign-in once; JobBee then never retries that password (accounts lock) and resets it with the site's "Forgot password" email, which also needs Gmail. The new password is saved like any other.
- Claude's search only covers the job boards listed in `pipeline/sources/web_search.py` plus your watchlist's career sites. It never scrapes LinkedIn or Indeed: those come in only through alert emails.
- Inside a form embedded from another site (for example, a Greenhouse form on a company page), the submit guard can't classify clicks. JobBee counts those clicks and blocks Enter.
