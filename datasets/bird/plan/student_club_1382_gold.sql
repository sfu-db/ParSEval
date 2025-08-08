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
    }
  ],
  "id": "6",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "GREATER_THAN",
        "operator": ">",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "INPUT_REF",
            "index": 3,
            "name": "$3",
            "type": "BIGINT"
          },
          {
            "kind": "LITERAL",
            "value": 7,
            "type": "INTEGER",
            "nullable": false,
            "precision": 10
          }
        ]
      },
      "variableset": "[]",
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
              "operator": "COUNT",
              "distinct": false,
              "ignoreNulls": false,
              "operands": [
                {
                  "column": 3,
                  "type": "VARCHAR"
                }
              ],
              "type": "BIGINT",
              "name": null
            }
          ],
          "id": "4",
          "inputs": [
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "INPUT_REF",
                  "index": 10,
                  "name": "$10",
                  "type": "VARCHAR"
                },
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
                  "index": 9,
                  "name": "$9",
                  "type": "VARCHAR"
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
                        "index": 10,
                        "name": "$10",
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
                      "table": "attendance",
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
  ]
}