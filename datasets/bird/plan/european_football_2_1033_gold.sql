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
  "id": "5",
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
          "name": "EXPR$1"
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "INPUT_REF",
              "index": 117,
              "name": "$117",
              "type": "VARCHAR"
            },
            {
              "kind": "INPUT_REF",
              "index": 0,
              "name": "$0",
              "type": "INTEGER"
            }
          ],
          "id": "3",
          "inputs": [
            {
              "relOp": "LogicalJoin",
              "joinType": "inner",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 2,
                    "name": "$2",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 115,
                    "name": "$115",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "Match",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "League",
                  "id": "1",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}