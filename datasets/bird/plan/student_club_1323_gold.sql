{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 1,
      "name": "$1",
      "type": "BIGINT"
    }
  ],
  "id": "7",
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
            "index": 2,
            "name": "$2",
            "type": "BIGINT"
          },
          {
            "kind": "LITERAL",
            "value": 10,
            "type": "INTEGER",
            "nullable": false,
            "precision": 10
          }
        ]
      },
      "variableset": "[]",
      "id": "6",
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
              "name": "EXPR$0"
            },
            {
              "operator": "COUNT",
              "distinct": false,
              "ignoreNulls": false,
              "operands": [
                {
                  "column": 2,
                  "type": "VARCHAR"
                }
              ],
              "type": "BIGINT",
              "name": null
            }
          ],
          "id": "5",
          "inputs": [
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "INPUT_REF",
                  "index": 3,
                  "name": "$3",
                  "type": "VARCHAR"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 0,
                  "name": "$0",
                  "type": "VARCHAR"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 7,
                  "name": "$7",
                  "type": "VARCHAR"
                }
              ],
              "id": "4",
              "inputs": [
                {
                  "relOp": "LogicalFilter",
                  "condition": {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 3,
                        "name": "$3",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "Meeting",
                        "type": "VARCHAR",
                        "nullable": false,
                        "precision": -1
                      }
                    ]
                  },
                  "variableset": "[]",
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
                            "index": 7,
                            "name": "$7",
                            "type": "VARCHAR"
                          }
                        ]
                      },
                      "id": "2",
                      "inputs": [
                        {
                          "relOp": "LogicalTableScan",
                          "table": "event",
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
  ]
}