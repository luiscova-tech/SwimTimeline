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
