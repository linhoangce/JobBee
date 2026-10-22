## LinkedIn notes

- The user is signed in to LinkedIn in this browser. If a login wall appears, call `request_human_action` with kind `login`.
- **Easy Apply** opens a modal wizard: contact info → resume → screening questions → Review. Advance with **Next** / **Review**. The last step's button is **Submit application**. Reaching that step means you are done: call `finish` with `ready_for_review`.
- On the resume step, LinkedIn may already list previous resumes. Upload the dossier's resume file and select it so this application uses it.
- Screening questions are often numeric ("How many years of experience with Python?"). Answer from the dossier. If the dossier doesn't say, give your best answer as a judgement call.
- Leave "Follow <company>" at its default unless the dossier says otherwise.
- If the posting shows **Apply** (not Easy Apply), it opens the employer's site in a new tab. Switch to that tab and continue there with that site's flow.
- Do not message recruiters, connect with people, or change the LinkedIn profile.
