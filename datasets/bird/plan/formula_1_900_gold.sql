{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 0,
      "name": "$0",
      "type": "VARCHAR"
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
            "value": 4,
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
                  "index": 2,
                  "name": "$2",
                  "type": "VARCHAR"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 9,
                  "name": "$9",
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
                        "kind": "GREATER_THAN_OR_EQUAL",
                        "operator": ">=",
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
                                        "index": 14,
                                        "name": "$14",
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
                            "value": "1990",
                            "type": "CHAR",
                            "nullable": false,
                            "precision": 4
                          }
                        ]
                      },
                      {
                        "kind": "LESS_THAN_OR_EQUAL",
                        "operator": "<=",
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
                                        "index": 14,
                                        "name": "$14",
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
                            "value": "2000",
                            "type": "CHAR",
                            "nullable": false,
                            "precision": 4
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
                            "index": 12,
                            "name": "$12",
                            "type": "INTEGER"
                          },
                          {
                            "kind": "INPUT_REF",
                            "index": 0,
                            "name": "$0",
                            "type": "INTEGER"
                          }
                        ]
                      },
                      "id": "2",
                      "inputs": [
                        {
                          "relOp": "LogicalTableScan",
                          "table": "circuits",
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