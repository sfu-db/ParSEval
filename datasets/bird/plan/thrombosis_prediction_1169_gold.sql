{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 2,
      "type": "DATE"
    }
  ],
  "dir": [
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "DATE"
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
                              "kind": "INPUT_REF",
                              "index": 48,
                              "name": "$48",
                              "type": "DATE"
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
                              "index": 46,
                              "name": "$46",
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
          "kind": "INPUT_REF",
          "index": 46,
          "name": "$46",
          "type": "DATE"
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
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 50,
                    "name": "$50",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "SJS",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              },
              {
                "kind": "NOT",
                "operator": "NOT",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "IS_NULL",
                    "operator": "IS NULL",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 46,
                        "name": "$46",
                        "type": "DATE"
                      }
                    ]
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
                    "index": 44,
                    "name": "$44",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "Laboratory",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "Patient",
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