{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 6,
      "name": "$6",
      "type": "VARCHAR"
    }
  ],
  "id": "7",
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
                "index": 0,
                "name": "$0",
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
                                  "index": 1,
                                  "name": "$1",
                                  "type": "DATE"
                                },
                                {
                                  "kind": "CAST",
                                  "operator": "CAST",
                                  "type": "DATE",
                                  "operands": [
                                    {
                                      "kind": "LITERAL",
                                      "value": "1997-01-27",
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
                                  "index": 7,
                                  "name": "$7",
                                  "type": "VARCHAR"
                                },
                                {
                                  "kind": "LITERAL",
                                  "value": "SLE",
                                  "type": "VARCHAR",
                                  "nullable": false,
                                  "precision": -1
                                }
                              ]
                            }
                          ]
                        },
                        "variableset": "[]",
                        "id": "1",
                        "inputs": [
                          {
                            "relOp": "LogicalTableScan",
                            "table": "Examination",
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
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 8,
                "name": "$8",
                "type": "DATE"
              },
              {
                "kind": "INPUT_REF",
                "index": 4,
                "name": "$4",
                "type": "DATE"
              }
            ]
          }
        ]
      },
      "variableset": "[]",
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
          "id": "5",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "Patient",
              "id": "3",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "Examination",
              "id": "4",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}