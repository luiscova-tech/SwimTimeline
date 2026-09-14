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

`data/current_meets.json` is public app data once deployed. Hosted Current Meets entries stay listed there until their `expires_at` date.

Uploaded meets are not added automatically. A successful upload has to be promoted with `Save To Current Meets`; after deployment, promoted documents should be treated as public documents.

Each Current Meets entry has `start_date`, `end_date`, and `expires_at`. The public list hides meets on `expires_at`, which is set to the day after the meet ends. The JSON record is left in place so the meet can still be archived or restored later.

## Usage Stats

The app can report aggregate usage at:

```text
https://swimtimeline.org/api/usage
```

(The Render subdomain, `swimtimeline.onrender.com`, still resolves to the same service and answers
the same endpoints -- it just isn't the canonical URL to hand out anymore.)

The counter stores total lookups and hashed normalized swimmer names, not swimmer names in plain text. It is useful for estimating distinct swimmer-name searches, but on Render's free filesystem it may reset after restarts or redeploys unless persistent storage is added later.

## Warm Monitoring

The app exposes a lightweight health endpoint:

```text
https://swimtimeline.org/api/health
```

Three cron-job.org jobs ping it every 10 minutes, scoped to when people actually check the site
rather than running continuously, to stay well under Render's 750 free instance-hours/month cap
(this schedule is ~430 hrs/month):

- `SwimTimeline warm — Mon-Thu`: hours 10-21, Mon/Tue/Wed/Thu (America/Phoenix)
- `SwimTimeline warm — Fri-Sat`: hours 5-23, Fri/Sat (America/Phoenix)
- `SwimTimeline warm — Sunday`: hours 5-17, Sun (America/Phoenix)

All three GET the health endpoint above -- never the homepage or a PDF parsing route. Outside
these windows the service is allowed to spin down normally; the first request after a gap still
pays the ~30-60s free-tier cold start.
