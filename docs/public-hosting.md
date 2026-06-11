# Public Hosting

SwimTimeline is a Python web service because PDF parsing runs on the backend. Static-only hosts are not enough for uploads and calendar generation.

## Recommended Free Path

Render currently supports Python web services on a free instance type. This repo includes:

- `requirements.txt`
- `render.yaml`

Deploy steps:

1. Push this project to a GitHub repository.
2. Create a new Render Blueprint or Web Service from the repository.
3. Use the included `render.yaml`.
4. Deploy.

Render will run:

```bash
pip install -r requirements.txt
python webapp/server.py --host 0.0.0.0
```

## Current Meets

`data/current_meets.json` is public app data once deployed. The Narwhal Invite stays listed there.

Uploaded meets are not added automatically. A successful upload has to be promoted with `Save To Current Meets`; after deployment, promoted documents should be treated as public documents.

## Optional Warm Monitoring

The app exposes a lightweight health endpoint:

```text
https://swimtimeline.onrender.com/api/health
```

If Render cold starts become annoying during a meet weekend, add an UptimeRobot HTTP monitor for that endpoint. Use the health endpoint, not the homepage and not a PDF parsing route.

Recommended setup:

- Monitor type: `HTTP(s)`
- URL: `https://swimtimeline.onrender.com/api/health`
- Interval: `14 minutes` if available, or the closest free interval
- Alerting: optional

Keep this off unless users complain about cold starts. A keep-warm monitor intentionally keeps the free service active, so it should be treated as a meet-weekend convenience rather than a permanent production strategy.
