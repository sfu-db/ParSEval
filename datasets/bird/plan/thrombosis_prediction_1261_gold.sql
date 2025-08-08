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
            "kind": "LESS_THAN",
            "operator": "<",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "CAST",
                "operator": "CAST",
                "type": "INTEGER",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 40,
                    "name": "$40",
                    "type": "VARCHAR"
                  }
                ]
              },
              {
                "kind": "LITERAL",
                "value": 20,
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
                                    "operator": "UDATE",
                                    "type": "DATE",
                                    "operands": [
                                      {
                                        "kind": "CAST",
                                        "operator": "CAST",
                                        "type": "TIMESTAMP",
                                        "operands": [
                                          {
                                            "kind": "LITERAL",
                                            "value": "now",
                                            "type": "CHAR",
                                            "nullable": false,
                                            "precision": 3
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
                "value": 60,
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