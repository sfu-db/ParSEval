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
                  "kind": "INPUT_REF",
                  "index": 0,
                  "name": "$0",
                  "type": "INTEGER"
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
  "id": "8",
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
      "id": "7",
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
                  "kind": "AND",
                  "operator": "AND",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "LESS_THAN",
                      "operator": "<",
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
                                      "index": 32,
                                      "name": "$32",
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
                          "value": "1985",
                          "type": "CHAR",
                          "nullable": false,
                          "precision": 4
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
                          "index": 10,
                          "name": "$10",
                          "type": "INTEGER"
                        },
                        {
                          "kind": "LITERAL",
                          "value": 50,
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
                  "value": 1,
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
            }
          ],
          "id": "6",
          "inputs": [
            {
              "relOp": "LogicalFilter",
              "condition": {
                "kind": "AND",
                "operator": "AND",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "GREATER_THAN_OR_EQUAL",
                    "operator": ">=",
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
                        "value": 2000,
                        "type": "INTEGER",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
                  },
                  {
                    "kind": "LESS_THAN_OR_EQUAL",
                    "operator": "<=",
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
                        "value": 2005,
                        "type": "INTEGER",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
                  }
                ]
              },
              "variableset": "[]",
              "id": "5",
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
                            "index": 18,
                            "name": "$18",
                            "type": "INTEGER"
                          }
                        ]
                      },
                      "id": "2",
                      "inputs": [
                        {
                          "relOp": "LogicalTableScan",
                          "table": "results",
                          "id": "0",
                          "inputs": []
                        },
                        {
                          "relOp": "LogicalTableScan",
                          "table": "races",
                          "id": "1",
                          "inputs": []
                        }
                      ]
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "drivers",
                      "id": "3",
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