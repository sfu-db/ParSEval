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
        "kind": "EQUALS",
        "operator": "=",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "INPUT_REF",
            "index": 1,
            "name": "$1",
            "type": "BIGINT"
          },
          {
            "kind": "LITERAL",
            "value": 2,
            "type": "BIGINT",
            "nullable": false,
            "precision": 19
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
              "name": "EXPR$0"
            }
          ],
          "id": "5",
          "inputs": [
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "INPUT_REF",
                  "index": 2,
                  "name": "$2",
                  "type": "INTEGER"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 1,
                  "name": "$1",
                  "type": "INTEGER"
                }
              ],
              "id": "4",
              "inputs": [
                {
                  "relOp": "LogicalFilter",
                  "condition": {
                    "kind": "AND",
                    "operator": "AND",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "EQUALS",
                        "operator": "=",
                        "type": "BOOLEAN",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 3,
                            "name": "$3",
                            "type": "FLOAT"
                          },
                          {
                            "kind": "LITERAL",
                            "value": 0.0,
                            "type": "FLOAT",
                            "nullable": false,
                            "precision": 15
                          }
                        ]
                      },
                      {
                        "kind": "EQUALS",
                        "operator": "=",
                        "type": "BOOLEAN",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 10,
                            "name": "$10",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "LITERAL",
                            "value": "Japanese",
                            "type": "VARCHAR",
                            "nullable": false,
                            "precision": -1
                          }
                        ]
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
                            "index": 2,
                            "name": "$2",
                            "type": "INTEGER"
                          },
                          {
                            "kind": "INPUT_REF",
                            "index": 7,
                            "name": "$7",
                            "type": "INTEGER"
                          }
                        ]
                      },
                      "id": "2",
                      "inputs": [
                        {
                          "relOp": "LogicalTableScan",
                          "table": "constructorStandings",
                          "id": "0",
                          "inputs": []
                        },
                        {
                          "relOp": "LogicalTableScan",
                          "table": "constructors",
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