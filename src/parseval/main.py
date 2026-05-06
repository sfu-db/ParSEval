from typing import List, Optional
from parseval.query import preprocess_sql


def disprove(
    q1,
    q2,
    schema,
    host_or_path,
    db_id,
    run_id=None,
    username=None,
    password=None,
    port=None,
    global_timeout=360,
    query_timeout=10,
    set_semantic=True,
    null_threshold=1,
    unique_threshold=1,
    duplicate_threshold=2,
    group_count_threshold=2,
    group_size_threshold=3,
    positive_threshold=2,
    negative_threshold=1,
    min_rows=3,
    max_tries=2,
    dialect="sqlite",
    existing_dbs: Optional[List] = None,
):
    import math

    from parseval.disprover import Disprover
    from parseval.configuration import DisproverConfig, GeneratorConfig
    from parseval.states import RunResult

    for label, query in [("q1", q1), ("q2", q2)]:
        if not isinstance(query, str) or (isinstance(query, float) and math.isnan(query)):
            return RunResult(
                q1=str(q1),
                q2=str(q2),
                host_or_path="N/A",
                db_id="N/A",
                q1_result=None,
                q2_result=None,
                state="ERR",
                set_semantic=set_semantic,
                error_msg=f"{label} is not a valid SQL string (got {type(query).__name__}: {query!r})",
            )

    generator_config = GeneratorConfig(
        null_threshold=null_threshold,
        unique_threshold=unique_threshold,
        duplicate_threshold=duplicate_threshold,
        group_count_threshold=group_count_threshold,
        group_size_threshold=group_size_threshold,
        positive_threshold=positive_threshold,
        negative_threshold=negative_threshold,
        min_rows=min_rows,
        max_tries=max_tries,
    )

    effective_db_id = f"{db_id}_{run_id}" if run_id is not None else db_id

    config = DisproverConfig(
        host_or_path=host_or_path,
        db_id=effective_db_id,
        username=username,
        password=password,
        port=port,
        global_timeout=global_timeout,
        query_timeout=query_timeout,
        set_semantic=set_semantic,
        generator=generator_config,
    )

    klass = Disprover(
        q1=q1,
        q2=q2,
        schema=schema,
        dialect=dialect,
        config=config,
        existing_dbs=existing_dbs,
        schema_db_id=db_id,
    )
    result = klass.run()
    return result


def instantiate_db(
    query,
    schema,
    host_or_path,
    db_id,
    username=None,
    password=None,
    port=None,
    global_timeout=360,
    query_timeout=10,
    null_threshold=1,
    unique_threshold=1,
    duplicate_threshold=2,
    group_count_threshold=2,
    group_size_threshold=3,
    positive_threshold=2,
    negative_threshold=1,
    min_rows=3,
    max_tries=2,
    dialect="sqlite",
):
    from parseval.speculative import SpeculativeGenerator
    from parseval.data_generator import DataGenerator
    from parseval.configuration import GeneratorConfig
    from parseval.instance import Instance
    from parseval.db_manager import DBManager
    import threading

    stop_event = threading.Event()

    def early_stop(instance: Instance) -> bool:
        instance.to_db(
            host_or_path=host_or_path,
            database=f"{instance.name}.sqlite",
            port=port,
            username=username,
            password=password,
            truncate_first=True,
        )

        with DBManager().get_connection(
            host_or_path=host_or_path,
            database=f"{instance.name}.sqlite",
            dialect=dialect,
            port=port,
            username=username,
            password=password,
        ) as conn:
            results = None
            try:
                results = conn.execute(query, fetch="all", timeout=query_timeout)
                return len(results) > 0
            except Exception as e:
                return False
        return None

    instance = Instance(ddls=schema, name=db_id, dialect=dialect)

    generator_config = GeneratorConfig(
        null_threshold=null_threshold,
        unique_threshold=unique_threshold,
        duplicate_threshold=duplicate_threshold,
        group_count_threshold=group_count_threshold,
        group_size_threshold=group_size_threshold,
        positive_threshold=positive_threshold,
        negative_threshold=negative_threshold,
        min_rows=min_rows,
        max_tries=max_tries,
    )

    expr = preprocess_sql(query, instance, dialect=dialect)

    spec = SpeculativeGenerator(expr, instance, generator_config=generator_config)
    spec.generate(
        early_stoper=early_stop,
        stop_event=stop_event,
        timeout=global_timeout,
    )
    # if early_stop(instance):
    #     return instance
    return instance
    # generator = DataGenerator(
    #     query,
    #     instance,
    #     verbose=False,
    #     config=generator_config,
    # )

    # generator.generate(early_stop=early_stop, stop_event=stop_event)

    # early_stop(instance)
