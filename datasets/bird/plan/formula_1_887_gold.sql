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
          "type": "INTEGER"
        }
      ],
      "aggs": [
        {
          "operator": "COUNT",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 1,
              "type": "INTEGER"
            }
          ],
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
              "index": 1,
              "name": "$1",
              "type": "INTEGER"
            },
            {
              "kind": "INPUT_REF",
              "index": 2,
              "name": "$2",
              "type": "INTEGER"
            }
          ],
          "id": "1",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "races",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}