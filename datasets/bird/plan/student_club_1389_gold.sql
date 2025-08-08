{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 3,
      "type": "INTEGER"
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
        },
        {
          "column": 1,
          "type": "VARCHAR"
        },
        {
          "column": 2,
          "type": "VARCHAR"
        }
      ],
      "aggs": [
        {
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 3,
              "type": "INTEGER"
            }
          ],
          "type": "INTEGER",
          "name": "DESC"
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "INPUT_REF",
              "index": 1,
              "name": "$1",
              "type": "VARCHAR"
            },
            {
              "kind": "INPUT_REF",
              "index": 2,
              "name": "$2",
              "type": "VARCHAR"
            },
            {
              "kind": "INPUT_REF",
              "index": 12,
              "name": "$12",
              "type": "VARCHAR"
            },
            {
              "kind": "INPUT_REF",
              "index": 11,
              "name": "$11",
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
                    "index": 0,
                    "name": "$0",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 14,
                    "name": "$14",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "member",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "income",
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