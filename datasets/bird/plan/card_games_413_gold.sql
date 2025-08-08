{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 4,
      "name": "$4",
      "type": "VARCHAR"
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
                "index": 7,
                "name": "$7",
                "type": "VARCHAR"
              }
            ],
            "query": [
              {
                "relOp": "LogicalProject",
                "project": [
                  {
                    "kind": "INPUT_REF",
                    "index": 71,
                    "name": "$71",
                    "type": "VARCHAR"
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
                              "index": 70,
                              "name": "$70",
                              "type": "VARCHAR"
                            },
                            {
                              "kind": "LITERAL",
                              "value": "Creature",
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
                              "index": 36,
                              "name": "$36",
                              "type": "VARCHAR"
                            },
                            {
                              "kind": "LITERAL",
                              "value": "normal",
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
                              "index": 4,
                              "name": "$4",
                              "type": "VARCHAR"
                            },
                            {
                              "kind": "LITERAL",
                              "value": "black",
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
                              "index": 1,
                              "name": "$1",
                              "type": "VARCHAR"
                            },
                            {
                              "kind": "LITERAL",
                              "value": "Matthew D. Wilson",
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
                        "table": "cards",
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
                "index": 2,
                "name": "$2",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "French",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
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
          "table": "foreign_data",
          "id": "3",
          "inputs": []
        }
      ]
    }
  ]
}