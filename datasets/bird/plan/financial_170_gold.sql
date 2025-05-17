{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "REAL",
      "operands": [
        {
          "kind": "TIMES",
          "operator": "*",
          "type": "REAL",
          "operands": [
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "REAL",
              "operands": [
                {
                  "kind": "MINUS",
                  "operator": "-",
                  "type": "INTEGER",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 0,
                      "name": "$0",
                      "type": "INTEGER"
                    },
                    {
                      "kind": "INPUT_REF",
                      "index": 1,
                      "name": "$1",
                      "type": "INTEGER"
                    }
                  ]
                }
              ]
            },
            {
              "kind": "LITERAL",
              "value": 100,
              "type": "INTEGER",
              "nullable": false,
              "precision": 10
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
                  "kind": "INPUT_REF",
                  "index": 1,
                  "name": "$1",
                  "type": "INTEGER"
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
              "kind": "INPUT_REF",
              "index": 1,
              "name": "$1",
              "type": "INTEGER"
            }
          ]
        }
      ]
    }
  ],
  "id": "10",
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
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 1,
              "type": "INTEGER"
            }
          ],
          "type": "INTEGER",
          "name": null
        }
      ],
      "id": "9",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
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
                      "kind": "OTHER_FUNCTION",
                      "operator": "STRFTIME",
                      "type": "VARCHAR",
                      "operands": [
                        {
                          "kind": "LITERAL",
                          "value": "%Y",
                          "type": "CHAR",
                          "nullable": false,
                          "precision": 2
                        },
                        {
                          "kind": "CAST",
                          "operator": "CAST",
                          "type": "VARCHAR",
                          "operands": [
                            {
                              "kind": "CAST",
                              "operator": "CAST",
                              "type": "TIMESTAMP",
                              "operands": [
                                {
                                  "kind": "INPUT_REF",
                                  "index": 2,
                                  "name": "$2",
                                  "type": "DATE"
                                }
                              ]
                            }
                          ]
                        }
                      ]
                    },
                    {
                      "kind": "LITERAL",
                      "value": "1997",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "INPUT_REF",
                  "index": 3,
                  "name": "$3",
                  "type": "INTEGER"
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
                      "kind": "OTHER_FUNCTION",
                      "operator": "STRFTIME",
                      "type": "VARCHAR",
                      "operands": [
                        {
                          "kind": "LITERAL",
                          "value": "%Y",
                          "type": "CHAR",
                          "nullable": false,
                          "precision": 2
                        },
                        {
                          "kind": "CAST",
                          "operator": "CAST",
                          "type": "VARCHAR",
                          "operands": [
                            {
                              "kind": "CAST",
                              "operator": "CAST",
                              "type": "TIMESTAMP",
                              "operands": [
                                {
                                  "kind": "INPUT_REF",
                                  "index": 2,
                                  "name": "$2",
                                  "type": "DATE"
                                }
                              ]
                            }
                          ]
                        }
                      ]
                    },
                    {
                      "kind": "LITERAL",
                      "value": "1996",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "INPUT_REF",
                  "index": 3,
                  "name": "$3",
                  "type": "INTEGER"
                },
                {
                  "kind": "LITERAL",
                  "value": 0,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
                }
              ]
            }
          ],
          "id": "8",
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
                        "index": 16,
                        "name": "$16",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "M",
                        "type": "VARCHAR",
                        "nullable": false,
                        "precision": -1
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
                        "index": 14,
                        "name": "$14",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "OWNER",
                        "type": "VARCHAR",
                        "nullable": false,
                        "precision": -1
                      }
                    ]
                  }
                ]
              },
              "variableset": "[]",
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
                        "index": 15,
                        "name": "$15",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 12,
                        "name": "$12",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "6",
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
                            "index": 13,
                            "name": "$13",
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
                      "id": "4",
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
                              "table": "loan",
                              "id": "0",
                              "inputs": []
                            },
                            {
                              "relOp": "LogicalTableScan",
                              "table": "account",
                              "id": "1",
                              "inputs": []
                            }
                          ]
                        },
                        {
                          "relOp": "LogicalTableScan",
                          "table": "disp",
                          "id": "3",
                          "inputs": []
                        }
                      ]
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "client",
                      "id": "5",
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