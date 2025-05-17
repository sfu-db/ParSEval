from src.pipeline.data_generation import data_generate
from src.corekit import get_ctx

if __name__ == '__main__':
    ctx = get_ctx(log_level = 'INFO', result_path = 'results/dail')
    dataset_fp = "datasets/bird/bird_dail2.jsonlines"
    workspace = ctx.result_path
    data_generate(dataset_fp= dataset_fp, workspace= workspace)