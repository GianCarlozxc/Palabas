# Watch

## Manual Deploy

After pushing your code to GitHub:

1. Open your GitHub repository.
2. Go to `Actions`.
3. Select `Manual Deploy`.
4. Click `Run workflow`.
5. Enter a new version, for example `1.0.1`.
6. Enter update notes.
7. Run the workflow.

The workflow builds `dist/Watch.exe`, updates `APP_VERSION` in `screen_share_party.py`, updates `update.json`, then commits and pushes those deploy files.

Installed apps check `update.json` on startup. If the version in GitHub is newer than the app version, the app shows an update notification with a progress bar.
