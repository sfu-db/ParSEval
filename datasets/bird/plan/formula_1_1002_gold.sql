{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 2,
      "type": "REAL"
    }
  ],
  "dir": [
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "10",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 13,
          "name": "$13",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 14,
          "name": "$14",
          "type": "VARCHAR"
        },
        {
          "kind": "PLUS",
          "operator": "+",
          "type": "REAL",
          "operands": [
            {
              "kind": "PLUS",
              "operator": "+",
              "type": "REAL",
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
                              "kind": "MINUS",
                              "operator": "-",
                              "type": "INTEGER",
                              "operands": [
                                {
                                  "kind": "OTHER_FUNCTION",
                                  "operator": "INSTR",
                                  "type": "INTEGER",
                                  "operands": [
                                    {
                                      "kind": "INPUT_REF",
                                      "index": 8,
                                      "name": "$8",
                                      "type": "VARCHAR"
                                    },
                                    {
                                      "kind": "LITERAL",
                                      "value": ":",
                                      "type": "CHAR",
                                      "nullable": false,
                                      "precision": 1
                                    }
                                  ]
                                },
                                {
                                  "kind": "LITERAL",
                                  "value": 1,
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
                  "type": "REAL",
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
                          "kind": "PLUS",
                          "operator": "+",
                          "type": "INTEGER",
                          "operands": [
                            {
                              "kind": "OTHER_FUNCTION",
                              "operator": "INSTR",
                              "type": "INTEGER",
                              "operands": [
                                {
                                  "kind": "INPUT_REF",
                                  "index": 8,
                                  "name": "$8",
                                  "type": "VARCHAR"
                                },
                                {
                                  "kind": "LITERAL",
                                  "value": ":",
                                  "type": "CHAR",
                                  "nullable": false,
                                  "precision": 1
                                }
                              ]
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
                        {
                          "kind": "MINUS",
                          "operator": "-",
                          "type": "INTEGER",
                          "operands": [
                            {
                              "kind": "MINUS",
                              "operator": "-",
                              "type": "INTEGER",
                              "operands": [
                                {
                                  "kind": "OTHER_FUNCTION",
                                  "operator": "INSTR",
                                  "type": "INTEGER",
                                  "operands": [
                                    {
                                      "kind": "INPUT_REF",
                                      "index": 8,
                                      "name": "$8",
                                      "type": "VARCHAR"
                                    },
                                    {
                                      "kind": "LITERAL",
                                      "value": ".",
                                      "type": "CHAR",
                                      "nullable": false,
                                      "precision": 1
                                    }
                                  ]
                                },
                                {
                                  "kind": "OTHER_FUNCTION",
                                  "operator": "INSTR",
                                  "type": "INTEGER",
                                  "operands": [
                                    {
                                      "kind": "INPUT_REF",
                                      "index": 8,
                                      "name": "$8",
                                      "type": "VARCHAR"
                                    },
                                    {
                                      "kind": "LITERAL",
                                      "value": ":",
                                      "type": "CHAR",
                                      "nullable": false,
                                      "precision": 1
                                    }
                                  ]
                                }
                              ]
                            },
                            {
                              "kind": "LITERAL",
                              "value": 1,
                              "type": "INTEGER",
                              "nullable": false,
                              "precision": 10
                            }
                          ]
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
                          "index": 8,
                          "name": "$8",
                          "type": "VARCHAR"
                        },
                        {
                          "kind": "PLUS",
                          "operator": "+",
                          "type": "INTEGER",
                          "operands": [
                            {
                              "kind": "OTHER_FUNCTION",
                              "operator": "INSTR",
                              "type": "INTEGER",
                              "operands": [
                                {
                                  "kind": "INPUT_REF",
                                  "index": 8,
                                  "name": "$8",
                                  "type": "VARCHAR"
                                },
                                {
                                  "kind": "LITERAL",
                                  "value": ".",
                                  "type": "CHAR",
                                  "nullable": false,
                                  "precision": 1
                                }
                              ]
                            },
                            {
                              "kind": "LITERAL",
                              "value": 1,
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
        }
      ],
      "id": "9",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "AND",
            "operator": "AND",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "NOT",
                "operator": "NOT",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "IS_NULL",
                    "operator": "IS NULL",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 8,
                        "name": "$8",
                        "type": "VARCHAR"
                      }
                    ]
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
                    "index": 19,
                    "name": "$19",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 2008,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              {
                "kind": "IN",
                "operator": "IN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 21,
                    "name": "$21",
                    "type": "INTEGER"
                  }
                ],
                "query": [
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
                    "id": "2",
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
                              "index": 2,
                              "name": "$2",
                              "type": "VARCHAR"
                            },
                            {
                              "kind": "LITERAL",
                              "value": "Marina Bay Street Circuit",
                              "type": "VARCHAR",
                              "nullable": false,
                              "precision": -1
                            }
                          ]
                        },
                        "variableset": "[]",
                        "id": "1",
                        "inputs": [
                          {
                            "relOp": "LogicalTableScan",
                            "table": "circuits",
                            "id": "0",
                            "inputs": []
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
          "id": "8",
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
                    "index": 18,
                    "name": "$18",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "7",
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
                        "index": 9,
                        "name": "$9",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "5",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "qualifying",
                      "id": "3",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "drivers",
                      "id": "4",
                      "inputs": []
                    }
                  ]
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "races",
                  "id": "6",
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