{
  "relOp": "LogicalAggregate",
  "keys": [
    {
      "column": 0,
      "type": "VARCHAR"
    }
  ],
  "aggs": [],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 3,
          "name": "$3",
          "type": "VARCHAR"
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
                                "index": 11,
                                "name": "$11",
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
                        "value": "2019-09-10",
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
                                "index": 11,
                                "name": "$11",
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
                "kind": "GREATER_THAN",
                "operator": ">",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 12,
                    "name": "$12",
                    "type": "FLOAT"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 20,
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
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 14,
                    "name": "$14",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "member",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "expense",
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