# Databricks Structured Streaming — operator runbook

Community Edition workspace, manual setup (CE's Jobs API doesn't
support Terraform management).

## 1. Provision AWS resources

```bash
cd infra && terraform apply
terraform output s3_bucket_name
terraform output databricks_s3_access_key_id
terraform output -raw databricks_s3_secret_key
terraform output kafka_bootstrap
```

Record all four values.

## 2. Install the Databricks CLI and configure the secret scope

```bash
pip install databricks-cli
databricks configure --token   # paste your CE workspace URL + a personal access token
databricks secrets create-scope weather-pipeline
databricks secrets put-secret weather-pipeline s3-access-key --string-value "<databricks_s3_access_key_id>"
databricks secrets put-secret weather-pipeline s3-secret-key --string-value "<databricks_s3_secret_key>"
```

## 3. Import the notebook

In the Databricks CE workspace UI: Workspace → Import →
`databricks/weather_processing.py` (source format: Python), and import
`databricks/transforms.py` into the same folder so the notebook's
`from transforms import ALERT_THRESHOLDS` resolves — Databricks adds a
notebook's own folder to `sys.path` automatically.

## 4. Create the scheduled Job

Workflows → Create Job:
- Task: Notebook task pointing at the imported `weather_processing.py`.
- Parameters (widgets): `kafka_bootstrap` = `<value from step 1>`,
  `s3_bucket` = `<value from step 1>`.
- Schedule: every 15 minutes.
- Cluster: a new job cluster, default CE instance size (single node —
  CE only offers single-node clusters).

## 5. Run once manually before leaving it on the schedule

Workflows → this Job → Run now. Watch the run; it should complete
within the 5-minute window without error.

## 6. Verify

See the plan's Task 5 for the full verification checklist (topic
contents, S3 objects, no-reprocessing check on a second run).
