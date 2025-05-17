{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 6,
      "name": "$6",
      "type": "INTEGER"
    },
    {
      "kind": "INPUT_REF",
      "index": 2,
      "name": "$2",
      "type": "FLOAT"
    },
    {
      "kind": "INPUT_REF",
      "index": 3,
      "name": "$3",
      "type": "FLOAT"
    }
  ],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
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
                              "index": 6,
                              "name": "$6",
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
                        },
                        {
                          "kind": "EQUALS",
                          "operator": "=",
                          "type": "BOOLEAN",
                          "operands": [
                            {
                              "kind": "INPUT_REF",
                              "index": 3,
                              "name": "$3",
                              "type": "DATE"
                            },
                            {
                              "kind": "CAST",
                              "operator": "CAST",
                              "type": "DATE",
                              "operands": [
                                {
                                  "kind": "LITERAL",
                                  "value": "1994-02-19",
                                  "type": "CHAR",
                                  "nullable": false,
                                  "precision": 10
                                }
                              ]
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
                        "table": "Patient",
                        "id": "0",
                        "inputs": []
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
                    "value": "1993-11-12",
                    "type": "CHAR",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "Examination",
          "id": "3",
          "inputs": []
        }
      ]
    }
  ]
}