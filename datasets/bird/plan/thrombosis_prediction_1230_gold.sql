{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": true,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
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
                "kind": "GREATER_THAN_OR_EQUAL",
                "operator": ">=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 20,
                    "name": "$20",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 200,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              {
                "kind": "GREATER_THAN",
                "operator": ">",
                "type": "BOOLEAN",
                "operands": [
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
                  "table": "Patient",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "Laboratory",
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