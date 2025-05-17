{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "BIGINT"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [
        {
          "column": 0,
          "type": "VARCHAR"
        }
      ],
      "aggs": [
        {
          "operator": "COUNT",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [],
          "type": "BIGINT",
          "name": "DESC"
        }
      ],
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "INPUT_REF",
              "index": 7,
              "name": "$7",
              "type": "VARCHAR"
            },
            {
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "INTEGER"
            }
          ],
          "id": "1",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "drivers",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}