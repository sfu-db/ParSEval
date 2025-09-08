{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "INTEGER"
    },
    {
      "column": 1,
      "type": "DECIMAL"
    }
  ],
  "aggs": [],
  "id": "11",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 0,
          "name": "$0",
          "type": "INTEGER"
        },
        {
          "kind": "MINUS",
          "operator": "-",
          "type": "DECIMAL",
          "operands": [
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "DECIMAL",
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
                              "kind": "OTHER_FUNCTION",
                              "operator": "CURRENT_TIMESTAMP",
                              "type": "TIMESTAMP",
                              "operands": []
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
              "kind": "CAST",
              "operator": "CAST",
              "type": "DECIMAL",
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
                }
              ]
            }
          ]
        }
      ],
      "id": "10",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "IN",
            "operator": "IN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 0,
                "name": "$0",
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
                "id": "5",
                "inputs": [
                  {
                    "relOp": "LogicalFilter",
                    "condition": {
                      "kind": "GREATER_THAN_OR_EQUAL",
                      "operator": ">=",
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
                          "type": "INTEGER",
                          "nullable": false,
                          "precision": 10
                        }
                      ]
                    },
                    "variableset": "[]",
                    "id": "4",
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
                                "column": 0,
                                "type": "INTEGER"
                              }
                            ],
                            "type": "BIGINT",
                            "name": null
                          }
                        ],
                        "id": "3",
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
                            "id": "2",
                            "inputs": [
                              {
                                "relOp": "LogicalFilter",
                                "condition": {
                                  "kind": "GREATER_THAN_OR_EQUAL",
                                  "operator": ">=",
                                  "type": "BOOLEAN",
                                  "operands": [
                                    {
                                      "kind": "INPUT_REF",
                                      "index": 19,
                                      "name": "$19",
                                      "type": "FLOAT"
                                    },
                                    {
                                      "kind": "LITERAL",
                                      "value": 52,
                                      "type": "INTEGER",
                                      "nullable": false,
                                      "precision": 10
                                    }
                                  ]
                                },
                                "variableset": "[]",
                                "id": "1",
                                "inputs": [
                                  {
                                    "relOp": "LogicalTableScan",
                                    "table": "Laboratory",
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
                  }
                ]
              }
            ]
          },
          "variableset": "[]",
          "id": "9",
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
                    "index": 7,
                    "name": "$7",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "8",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "Patient",
                  "id": "6",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "Laboratory",
                  "id": "7",
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