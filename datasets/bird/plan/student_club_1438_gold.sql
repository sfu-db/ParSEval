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
    }
  ],
  "aggs": [],
  "id": "7",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 5,
          "name": "$5",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 16,
          "name": "$16",
          "type": "VARCHAR"
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
                    "kind": "OTHER_FUNCTION",
                    "operator": "UDATE",
                    "type": "DATE",
                    "operands": [
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "TIMESTAMP",
                        "operands": [
                          {
                            "kind": "OTHER_FUNCTION",
                            "operator": "SUBSTR",
                            "type": "VARCHAR",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 2,
                                "name": "$2",
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
                                "value": 10,
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
                    "kind": "CAST",
                    "operator": "CAST",
                    "type": "DATE",
                    "operands": [
                      {
                        "kind": "LITERAL",
                        "value": "2019-01-10",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 10
                      }
                    ]
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
                    "operator": "UDATE",
                    "type": "DATE",
                    "operands": [
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "TIMESTAMP",
                        "operands": [
                          {
                            "kind": "OTHER_FUNCTION",
                            "operator": "SUBSTR",
                            "type": "VARCHAR",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 2,
                                "name": "$2",
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
                                "value": 10,
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
                    "kind": "CAST",
                    "operator": "CAST",
                    "type": "DATE",
                    "operands": [
                      {
                        "kind": "LITERAL",
                        "value": "2019-11-19",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 10
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
                    "index": 4,
                    "name": "$4",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "true",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
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
                    "index": 7,
                    "name": "$7",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 17,
                    "name": "$17",
                    "type": "VARCHAR"
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
                        "index": 5,
                        "name": "$5",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 7,
                        "name": "$7",
                        "type": "VARCHAR"
                      }
                    ]
                  },
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "expense",
                      "id": "0",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "member",
                      "id": "1",
                      "inputs": []
                    }
                  ]
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "attendance",
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