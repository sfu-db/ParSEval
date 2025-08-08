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
  "id": "8",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
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
                "index": 1,
                "name": "$1",
                "type": "INTEGER"
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
                      "kind": "EQUALS",
                      "operator": "=",
                      "type": "BOOLEAN",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 14,
                          "name": "$14",
                          "type": "INTEGER"
                        },
                        {
                          "kind": "LITERAL",
                          "value": 1,
                          "type": "INTEGER",
                          "nullable": false,
                          "precision": 10
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
                                            "index": 4,
                                            "name": "$4",
                                            "type": "VARCHAR"
                                          },
                                          {
                                            "kind": "LITERAL",
                                            "value": "Lewis",
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
                                            "index": 5,
                                            "name": "$5",
                                            "type": "VARCHAR"
                                          },
                                          {
                                            "kind": "LITERAL",
                                            "value": "Hamilton",
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
                                      "table": "drivers",
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
                    }
                  ]
                },
                "variableset": "[]",
                "id": "4",
                "inputs": [
                  {
                    "relOp": "LogicalTableScan",
                    "table": "results",
                    "id": "3",
                    "inputs": []
                  }
                ]
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "7",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "races",
          "id": "6",
          "inputs": []
        }
      ]
    }
  ]
}