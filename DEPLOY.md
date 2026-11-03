# Deploying the prototype to Streamlit Community Cloud

The hosted app is protected so the link cannot spend your Anthropic credits:

- Anyone with the link can open the three protocols already extracted (NCT04736199,
  NCT06192108, NCT05882357, from `protocols/` or by NCT ID) and run the free checks.
- New extractions and chat questions need either the **demo password** (uses your key)
  or the visitor's **own Anthropic API key** (pasted in the sidebar, used for that session only).
- Your key lives only in Streamlit Cloud's Secrets box. It is never in the code or the repo.

## Before you start (one time, about 10 minutes)

1. **Run the paid extractions once locally**, so the demo is free:
   `py -m streamlit run app.py`, open each protocol you want to show, and press the extract
   buttons (study design is about $0.25 per protocol). Results go into `cache/`.
2. In the Anthropic Console (console.anthropic.com > Settings > Limits), set a **monthly
   spend limit** on the key you will use, as a safety net.
3. Install Git if `git --version` fails in PowerShell: https://git-scm.com/download/win

## 1. Create the GitHub repository

1. Go to https://github.com/new (signed in as NavG-24).
2. Name: `protocol-assistant`. Choose **Private** (Streamlit Cloud can deploy private repos,
   and the code then stays out of public view). Do **not** add a README or .gitignore there.
3. Click **Create repository**.

## 2. Push the prototype

In PowerShell:

```powershell
cd C:\Users\gupta\Downloads\ai-product-milestone-2-package\supporting\prototype
git init
git add .
git status
```

Check the `git status` list: it should show `cache/`, `protocols/`, the `.py` files and
`.streamlit/secrets.toml.example`, and **no** `secrets.toml` or `__pycache__`. Then:

```powershell
git commit -m "Protocol assistant prototype"
git branch -M main
git remote add origin https://github.com/NavG-24/protocol-assistant.git
git push -u origin main
```

A browser window asks you to sign in to GitHub the first time.

## 3. Deploy on Streamlit Community Cloud

1. Go to https://share.streamlit.io and **Continue with GitHub**. Allow access to private
   repos when asked.
2. Click **Create app** > **Deploy a public app from GitHub** (or "from repo").
3. Repository `NavG-24/protocol-assistant`, branch `main`, main file `app.py`.
   Pick an App URL, e.g. `protocol-assistant-demo`.
4. Open **Advanced settings**: Python version **3.12**, and paste into **Secrets**:

   ```toml
   ANTHROPIC_API_KEY = "sk-ant-...your key..."
   APP_PASSWORD = "a password you will give Abhishek"
   ```

5. Click **Deploy**. The first build takes 2-5 minutes.

To change the key or password later: your app's **⋮ > Settings > Secrets**, save, and the
app restarts.

## 4. Test before sharing

1. Open the app link in a private browser window.
2. Without a password, fetch `NCT04736199`: saved study fields should appear with no cost.
   (Saved results are matched to the exact PDF file. If nothing saved shows, the registry's
   copy differs from your local file; upload `protocols/NCT04736199.pdf` instead, which matches.)
3. Enter a wrong password: "Wrong password." Then the right one: "Unlocked".
4. Ask one chat question (a cent or two) and check the answer cites a page.
5. On **Checks**, click **Fetch the registry record** to confirm ClinicalTrials.gov works.

## 5. Share

Send Abhishek the link, and the password in a separate message.

## Good to know

- **Sharing setting:** in the app's Settings > Sharing, the app can be public (anyone with
  the link) or limited to invited emails. Public plus the password is the simplest.
- **New extractions made on the hosted app are not kept** after it restarts or sleeps
  (Cloud storage is temporary). To keep one, run it locally and push the new `cache/` files:
  `git add cache && git commit -m "Add extractions" && git push`. The app redeploys itself.
- **Sleeping:** an app with no visitors for a few days goes to sleep; the first visitor
  clicks "Yes, get this app back up" and waits about a minute. Open it yourself before a demo.
- Any push to `main` redeploys the app automatically.
