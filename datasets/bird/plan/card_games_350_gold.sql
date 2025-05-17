{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 3,
      "type": "BIGINT"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "7",
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
          "index": 0,
          "name": "$0",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 2,
          "name": "$2",
          "type": "INTEGER"
        },
        {
          "kind": "INPUT_REF",
          "index": 3,
          "name": "$3",
          "type": "BIGINT"
        }
      ],
      "id": "6",
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
              "type": "INTEGER"
            }
          ],
          "aggs": [
            {
              "operator": "COUNT",
              "distinct": true,
              "ignoreNulls": false,
              "operands": [
                {
                  "column": 3,
                  "type": "VARCHAR"
                }
              ],
              "type": "BIGINT",
              "name": "DESC"
            }
          ],
          "id": "5",
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
                  "index": 48,
                  "name": "$48",
                  "type": "VARCHAR"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 28,
                  "name": "$28",
                  "type": "INTEGER"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 71,
                  "name": "$71",
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
                        "index": 28,
                        "name": "$28",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "LITERAL",
                        "value": 1,
                        "type": "INTEGER",
                        "nullable": false,
                        "precision": 10
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
                            "index": 71,
                            "name": "$71",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "INPUT_REF",
                            "index": 77,
                            "name": "$77",
                            "type": "VARCHAR"
                          }
                        ]
                      },
                      "id": "2",
                      "inputs": [
                        {
                          "relOp": "LogicalTableScan",
                          "table": "cards",
                          "id": "0",
                          "inputs": []
                        },
                        {
                          "relOp": "LogicalTableScan",
                          "table": "rulings",
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