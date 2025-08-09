import wandb
import os

host = os.environ.get("WANDB_HOST", "http://wandb-local:8080")
# host = "http://172.21.0.3:8080"
wandb.login(key=os.environ.get("WANDB_API_KEY"), host=host)

wandb.init(project="ParSEval")

table = wandb.Table(columns=["step", "status", "message"])

for i in range(5):
    status = "processing" if i < 3 else "done"
    message = f"Iteration {i} running"
    table.add_data(i, status, message)

wandb.log({"progress_table": table})

wandb.finish()