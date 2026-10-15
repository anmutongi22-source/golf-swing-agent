# Golf Swing Coach

Upload a video (or photo) of a golf swing and get scores, strengths, fixes tied to
specific frames, drills, and one key tip. Runs locally or on Azure App Service.
No Docker required.

How it works: the service pulls six evenly spaced frames from the video (address
through finish), sends them to Claude through Azure AI Foundry (or the Anthropic
API), and returns structured coaching JSON. The web page shows the frames as a
filmstrip; clicking "Frame 3" next to a note highlights that frame.

## Files

| Path | Purpose |
|---|---|
| `app/main.py` | Web service: page, health checks, upload endpoint |
| `app/video.py` | Pulls frames out of the uploaded video |
| `app/coach.py` | Calls the model and checks its reply |
| `app/prompts/coach.md` | The coaching instructions given to the model |
| `app/static/index.html` | The upload page |
| `run-local.ps1` | Install and run on your machine |
| `deploy.ps1` | Create the Azure resources and deploy |

## Run on your machine

You need Python 3.11 or newer (`winget install --exact --id Python.Python.3.12`).

```powershell
cd $HOME\agent-platform\golf-swing-agent
Copy-Item .env.example .env
notepad .env          # paste your FOUNDRY_API_KEY and save
.\run-local.ps1 -Test # first run installs packages, then runs the tests
.\run-local.ps1       # starts the app
```

Open http://127.0.0.1:8090 and upload a swing.

If PowerShell says running scripts is disabled, run this once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

In VS Code: open the folder, pick the `.venv` interpreter when prompted, then press
F5 and choose "Golf Swing Coach (local)" to run it with the debugger.

## Deploy to Azure

```powershell
cd $HOME\agent-platform\golf-swing-agent
.\deploy.ps1 -AppName golf-swing-<yourname>
```

The script signs you in, finds the Foundry key (or asks for it), creates a resource
group, App Service plan (B1, Linux) and web app, uploads the code, and waits for
readiness to flip from 503 to 200. Re-run the same command to deploy changes.

Useful options: `-Subscription "<name>"`, `-ResourceGroup`, `-Location`,
`-Provider anthropic` (uses an Anthropic API key instead of Foundry).

## Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/` | Upload page |
| GET | `/management/health/liveness` | Always 200 when running |
| GET | `/management/health/readiness` | 200 when configured, 503 with the reason otherwise |
| POST | `/api/golf-swing/analyze` | Multipart form: `file`, `swing_type`, `skill_level`, `concerns` |

Calling the API from PowerShell 7:

```powershell
Invoke-RestMethod https://<app>.azurewebsites.net/api/golf-swing/analyze -Method Post -Form @{
    file        = Get-Item .\my-swing.mp4
    swing_type  = "Driver"
    skill_level = "Beginner"
    concerns    = "I slice it"
}
```

(`-Form` needs PowerShell 7. In Windows PowerShell 5.1 use `curl.exe -F "file=@my-swing.mp4" -F "swing_type=Driver" <url>`.)

## Troubleshooting

- **Readiness returns 503**: the response lists what's missing, usually the API key.
  Fix it in `.env` locally, or in Azure: Portal > your web app > Environment variables.
- **"The model endpoint returned HTTP 404"**: the Foundry endpoint or model name is
  wrong. In the Foundry portal, open your Claude deployment, copy its endpoint URL and
  deployment name, and set `FOUNDRY_ENDPOINT` and `MODEL_ID` to match.
- **"HTTP 401"**: the key is wrong or belongs to another resource.
- **iPhone videos won't open**: they're often HEVC. Set Camera > Formats to Most
  Compatible, or export the clip as MP4 (H.264).
- **Azure logs**: `az webapp log tail --name <app> --resource-group rg-golf-swing-coach`

## Security notes

The API key is stored as an App Service app setting, which is encrypted at rest but
visible to anyone with access to the web app. For production, move it to Azure Key
Vault and reference it from the app setting. The upload page has no sign-in; anyone
with the URL can use it and spend your model quota. Turn on App Service
Authentication (Portal > Authentication) to restrict it to your organisation.
