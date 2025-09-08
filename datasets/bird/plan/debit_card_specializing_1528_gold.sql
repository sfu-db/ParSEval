{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "FLOAT"
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
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 1,
              "type": "FLOAT"
            }
          ],
          "type": "FLOAT",
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
              "index": 5,
              "name": "$5",
              "type": "INTEGER"
            },
            {
              "kind": "INPUT_REF",
              "index": 8,
              "name": "$8",
              "type": "FLOAT"
            }
          ],
          "id": "1",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "transactions_1k",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}