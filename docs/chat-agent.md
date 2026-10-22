# JobBee in Telegram (optional)

Telegram mirrors the web app ([ui-guide.md](ui-guide.md)) on your phone.
- You get the same cards: new matches with their CV, review & submit, and jobs set aside for when you're at your PC.
- You can tap Apply, Skip or Submit there.
- Sending the bot a job link works like pasting it into the agent bar.

Whichever side you act on first wins; the other side's buttons stop working.

## Before you start

- **Claude:** connect it in the welcome flow or Settings, and make sure your Anthropic account has credit.
  - Haiku 5.5 screens.
  - Sonnet 5.5 tailors the CV.
  - `APPLY_MODEL` fills the form (Opus 5.5 by default).
- **Your resume:** upload it in JobBee's welcome flow. It's the base for every tailored CV.
- **Google Chrome:** it must be installed. JobBee uses its own profile (`backend/data/browser-chrome`), so you sign in to LinkedIn or Workday there once.

## Run it

Start the backend **without `--reload`**. A reload restarts the server and interrupts any application in progress. From `backend\`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then start the frontend from `frontend\` with `npm.cmd run dev` (it's only needed for setup).

## Connect Telegram (once)

1. In Telegram, open **@BotFather** and send `/newbot`. Pick a name and a username ending in `bot`, then copy the token.
2. Open http://localhost:5173/settings#phone, paste the token and click **Connect**.
   - The token is stored in Windows Credential Manager, not in `.env`.
3. Click **Open Telegram** and press **Start**. The page switches to "Connected ✓", and the bot says hello.

Commands: `/status`, `/pause`, `/resume`, `/help`.

## Try the flow

1. **Send the bot a job link.** Greenhouse, Lever and Ashby links read best; Workday and company sites work through the page's job data.
2. Within about a minute you get:
   - a **card** with the match score, why it fits, the gaps and what changed in your CV;
   - the tailored **CV PDF**;
   - **Apply / Skip / Posting** buttons.
3. **Skip** asks why. Picking "This company" means you won't see that company again.
4. **Apply** sends Claude to fill the form in Chrome on your PC, by itself:
   - It creates or signs in to the site's account (Settings → Applying for you) and confirms the email from your connected inbox.
   - It answers every question from your profile. Where your profile has no answer it makes a judgement call and flags it.
   - A CAPTCHA, a phone code or a required ID/bank field **sets the job aside** with **I'm at my PC** / **Skip it** buttons; the other applications carry on. **I'm at my PC** tries again and hands you the browser for that step; tap **Done** when you've done it.
5. When the form is filled you get a **screenshot** and **everything it filled in** (🤔 its judgement calls, ✍️ consents it gave), with **Submit / Don't submit**. To change an answer, use **Change** on the web card.
   - Its judgement calls and your changes are saved when you tap Submit, so it answers the same way next time.
   - The form stays open for 45 minutes (`SUBMIT_TTL_MIN`). After that it closes and offers **Fill it again**.
6. **Submit:** JobBee clicks the page's single final-submit button, once, and only on the exact page you reviewed. It then looks for a confirmation.
   - Either you get "✅ Applied", or it asks you to check the tab: **It went through / It didn't**.

The Home page (http://localhost:5173) shows the same cards, if you're at the PC.

## Limits right now

- **Your PC must be on with JobBee running.** One application is filled at a time; up to 2 filled forms can wait for your Submit tap.
- A hand-off ("needs you at your PC") pauses the queue until you tap Done.
- **The submit guard is blind inside cross-origin embedded forms.** It can't classify clicks inside forms embedded from another domain, such as Greenhouse embedded on a company site. Those clicks are counted, and Enter is blocked there.
- **Spend:** every Claude call is logged in `llm_usage`. The defaults are `DAILY_BUDGET_USD=10` and `MONTHLY_BUDGET_USD=100`, and `/status` shows today's spend.
