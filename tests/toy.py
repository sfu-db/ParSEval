import pandas as pd

df = pd.read_csv("/workspace/datasets/beers/beers.csv")

df.rename({"index": "question_id"})

df.to_json("/workspace/datasets/beers/beers.json", orient="records", indent=2)
