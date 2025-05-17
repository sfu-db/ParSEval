{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "VARCHAR"
    }
  ],
  "aggs": [],
  "id": "12",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 28,
          "name": "$28",
          "type": "VARCHAR"
        }
      ],
      "id": "11",
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
                    "kind": "OTHER_FUNCTION",
                    "operator": "SUBSTR",
                    "type": "VARCHAR",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 3,
                        "name": "$3",
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
                        "value": 4,
                        "type": "INTEGER",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
                  },
                  {
                    "kind": "LITERAL",
                    "value": "2012",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              },
              {
                "kind": "GREATER_THAN",
                "operator": ">",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 8,
                    "name": "$8",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "SCALAR_QUERY",
                    "operator": "$SCALAR_QUERY",
                    "operands": [],
                    "query": [
                      {
                        "relOp": "LogicalProject",
                        "project": [
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
                                    "kind": "INPUT_REF",
                                    "index": 0,
                                    "name": "$0",
                                    "type": "INTEGER"
                                  }
                                ]
                              },
                              {
                                "kind": "CASE",
                                "operator": "CASE",
                                "type": "BIGINT",
                                "operands": [
                                  {
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
                                        "value": 0,
                                        "type": "BIGINT",
                                        "nullable": false,
                                        "precision": 19
                                      }
                                    ]
                                  },
                                  {
                                    "kind": "LITERAL",
                                    "value": "NULL",
                                    "type": "BIGINT",
                                    "nullable": true,
                                    "precision": 19
                                  },
                                  {
                                    "kind": "CAST",
                                    "operator": "CAST",
                                    "type": "BIGINT",
                                    "operands": [
                                      {
                                        "kind": "INPUT_REF",
                                        "index": 1,
                                        "name": "$1",
                                        "type": "BIGINT"
                                      }
                                    ]
                                  }
                                ]
                              }
                            ]
                          }
                        ],
                        "id": "6",
                        "inputs": [
                          {
                            "relOp": "LogicalAggregate",
                            "keys": [],
                            "aggs": [
                              {
                                "operator": "SUM",
                                "distinct": false,
                                "ignoreNulls": false,
                                "operands": [
                                  {
                                    "column": 0,
                                    "type": "INTEGER"
                                  }
                                ],
                                "type": "INTEGER",
                                "name": null
                              },
                              {
                                "operator": "COUNT",
                                "distinct": false,
                                "ignoreNulls": false,
                                "operands": [],
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
                                    "index": 13,
                                    "name": "$13",
                                    "type": "INTEGER"
                                  },
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
                                      "kind": "EQUALS",
                                      "operator": "=",
                                      "type": "BOOLEAN",
                                      "operands": [
                                        {
                                          "kind": "OTHER_FUNCTION",
                                          "operator": "SUBSTR",
                                          "type": "VARCHAR",
                                          "operands": [
                                            {
                                              "kind": "INPUT_REF",
                                              "index": 8,
                                              "name": "$8",
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
                                              "value": 4,
                                              "type": "INTEGER",
                                              "nullable": false,
                                              "precision": 10
                                            }
                                          ]
                                        },
                                        {
                                          "kind": "LITERAL",
                                          "value": "2012",
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
                                              "index": 1,
                                              "name": "$1",
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
                                            "table": "Team",
                                            "id": "0",
                                            "inputs": []
                                          },
                                          {
                                            "relOp": "LogicalTableScan",
                                            "table": "Team_Attributes",
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
                ]
              }
            ]
          },
          "variableset": "[]",
          "id": "10",
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
                    "index": 26,
                    "name": "$26",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "9",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "Team_Attributes",
                  "id": "7",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "Team",
                  "id": "8",
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