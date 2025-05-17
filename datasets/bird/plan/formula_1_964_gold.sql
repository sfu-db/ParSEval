{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [],
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
          "index": 0,
          "name": "$0",
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
                    "index": 7,
                    "name": "$7",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "French",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              },
              {
                "kind": "LESS_THAN",
                "operator": "<",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "PLUS",
                    "operator": "+",
                    "type": "REAL",
                    "operands": [
                      {
                        "kind": "PLUS",
                        "operator": "+",
                        "type": "INTEGER",
                        "operands": [
                          {
                            "kind": "TIMES",
                            "operator": "*",
                            "type": "INTEGER",
                            "operands": [
                              {
                                "kind": "CAST",
                                "operator": "CAST",
                                "type": "INTEGER",
                                "operands": [
                                  {
                                    "kind": "OTHER_FUNCTION",
                                    "operator": "SUBSTR",
                                    "type": "VARCHAR",
                                    "operands": [
                                      {
                                        "kind": "INPUT_REF",
                                        "index": 13,
                                        "name": "$13",
                                        "type": "VARCHAR"
                                      },
                                      {
                                        "kind": "LITERAL",
                                        "value": 1,
                                        "type": "INTEGER",
                                        "nullable": false,
                                        "precision": 10
                                      },
                                      {
                                        "kind": "LITERAL",
                                        "value": 2,
                                        "type": "INTEGER",
                                        "nullable": false,
                                        "precision": 10
                                      }
                                    ]
                                  }
                                ]
                              },
                              {
                                "kind": "LITERAL",
                                "value": 60,
                                "type": "INTEGER",
                                "nullable": false,
                                "precision": 10
                              }
                            ]
                          },
                          {
                            "kind": "CAST",
                            "operator": "CAST",
                            "type": "INTEGER",
                            "operands": [
                              {
                                "kind": "OTHER_FUNCTION",
                                "operator": "SUBSTR",
                                "type": "VARCHAR",
                                "operands": [
                                  {
                                    "kind": "INPUT_REF",
                                    "index": 13,
                                    "name": "$13",
                                    "type": "VARCHAR"
                                  },
                                  {
                                    "kind": "LITERAL",
                                    "value": 4,
                                    "type": "INTEGER",
                                    "nullable": false,
                                    "precision": 10
                                  },
                                  {
                                    "kind": "LITERAL",
                                    "value": 2,
                                    "type": "INTEGER",
                                    "nullable": false,
                                    "precision": 10
                                  }
                                ]
                              }
                            ]
                          }
                        ]
                      },
                      {
                        "kind": "DIVIDE",
                        "operator": "/",
                        "type": "REAL",
                        "operands": [
                          {
                            "kind": "CAST",
                            "operator": "CAST",
                            "type": "REAL",
                            "operands": [
                              {
                                "kind": "OTHER_FUNCTION",
                                "operator": "SUBSTR",
                                "type": "VARCHAR",
                                "operands": [
                                  {
                                    "kind": "INPUT_REF",
                                    "index": 13,
                                    "name": "$13",
                                    "type": "VARCHAR"
                                  },
                                  {
                                    "kind": "LITERAL",
                                    "value": 7,
                                    "type": "INTEGER",
                                    "nullable": false,
                                    "precision": 10
                                  },
                                  {
                                    "kind": "LITERAL",
                                    "value": 2,
                                    "type": "INTEGER",
                                    "nullable": false,
                                    "precision": 10
                                  }
                                ]
                              }
                            ]
                          },
                          {
                            "kind": "CASE",
                            "operator": "CASE",
                            "type": "INTEGER",
                            "operands": [
                              {
                                "kind": "EQUALS",
                                "operator": "=",
                                "type": "BOOLEAN",
                                "operands": [
                                  {
                                    "kind": "LITERAL",
                                    "value": 1000,
                                    "type": "INTEGER",
                                    "nullable": false,
                                    "precision": 10
                                  },
                                  {
                                    "kind": "LITERAL",
                                    "value": 0,
                                    "type": "INTEGER",
                                    "nullable": false,
                                    "precision": 10
                                  }
                                ]
                              },
                              {
                                "kind": "LITERAL",
                                "value": "NULL",
                                "type": "INTEGER",
                                "nullable": true,
                                "precision": 10
                              },
                              {
                                "kind": "LITERAL",
                                "value": 1000,
                                "type": "INTEGER",
                                "nullable": false,
                                "precision": 10
                              }
                            ]
                          }
                        ]
                      }
                    ]
                  },
                  {
                    "kind": "LITERAL",
                    "value": 120,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
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
                    "index": 0,
                    "name": "$0",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 10,
                    "name": "$10",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "drivers",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "lapTimes",
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